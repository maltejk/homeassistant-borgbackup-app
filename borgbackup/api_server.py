"""
BorgBackup Add-on – HTTP API server.

Provides a REST API on port 8774 so the Home Assistant BorgBackup
integration can upload, download, list, and delete backups.

Environment variables (set by run.sh):
    BORGBACKUP_API_KEY       Bearer token for authentication
    BORGBACKUP_REPO_PATH     Borg repository path (e.g. user@host:repo)
    BORGBACKUP_PASSPHRASE    Borg repository passphrase
    BORGBACKUP_KEEP_DAILY    Retention: keep N daily archives
    BORGBACKUP_KEEP_WEEKLY   Retention: keep N weekly archives
    BORGBACKUP_KEEP_MONTHLY  Retention: keep N monthly archives

API:
    GET  /api/health                  → 200 {"status": "ok"}
    GET  /api/backups                 → 200 [<backup-meta-dict>, ...]
    POST /api/backups                 → 201 (headers: X-Backup-Id, X-Backup-Meta)
    GET  /api/backups/{backup_id}     → 200 (streaming tar body)
    DELETE /api/backups/{backup_id}   → 204
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import logging
import os
import re
import subprocess
from pathlib import Path

from aiohttp import web

_LOG = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ---------------------------------------------------------------------------
# Configuration from environment
# ---------------------------------------------------------------------------

API_KEY = os.environ["BORGBACKUP_API_KEY"]
REPO_PATH = os.environ["BORGBACKUP_REPO_PATH"]
PASSPHRASE = os.environ.get("BORGBACKUP_PASSPHRASE", "")
KEEP_DAILY = int(os.environ.get("BORGBACKUP_KEEP_DAILY", "7"))
KEEP_WEEKLY = int(os.environ.get("BORGBACKUP_KEEP_WEEKLY", "4"))
KEEP_MONTHLY = int(os.environ.get("BORGBACKUP_KEEP_MONTHLY", "6"))
INIT_IF_NOT_EXISTS = (
    os.environ.get("BORGBACKUP_INIT_IF_NOT_EXISTS", "true").lower() == "true"
)

CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming chunks
ARCHIVE_PREFIX = "ha-"

# How long a borg command waits to acquire the repository/cache lock before
# giving up (borg's own default is 1s, far too short for a multi-minute
# upload to still be running when HA polls the backup list).
LOCK_WAIT = "600"

# Ceiling for borg commands that should always be quick (list/info). Lower
# than LOCK_WAIT would allow, but still generous enough to ride out the rare
# case of a concurrent external `borg` process (e.g. a manual CLI run)
# holding the repo lock.
QUICK_TIMEOUT = 120

# borg only locks per-command; a second command against the same repo can
# still race the first one (e.g. `list` while an upload is mid-`create`).
# Serialize all borg invocations through this add-on process with a single
# lock so concurrent HTTP requests never hit each other's repo lock.
_BORG_LOCK = asyncio.Lock()

# HA backup IDs become part of an archive name and (until the streaming
# rewrite) a filesystem path; only accept the conservative charset HA itself
# generates (uuid4 hex) to rule out both path traversal and archive-name
# injection via a crafted X-Backup-Id/backup_id.
BACKUP_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


# ---------------------------------------------------------------------------
# Comment (metadata) encoding
# ---------------------------------------------------------------------------
#
# The HA backup metadata is stored in the Borg archive *comment*. Borg runs the
# comment through its placeholder engine on ``create``, so any literal ``{`` /
# ``}`` (which JSON is full of) is interpreted as a placeholder and rejected
# with "Invalid placeholder". To store arbitrary JSON safely we base64-encode
# it on write and decode it on read.


def _encode_comment(meta_json: str) -> str:
    """Encode metadata JSON for safe storage in a Borg archive comment."""
    return base64.b64encode(meta_json.encode("utf-8")).decode("ascii")


def _decode_comment(comment: str) -> str:
    """Decode a stored comment back to metadata JSON.

    Falls back to treating the comment as raw JSON for forward/backward
    compatibility with archives that were not base64-encoded.
    """
    try:
        return base64.b64decode(comment.encode("ascii"), validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError):
        return comment


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------


@web.middleware
async def _auth_middleware(
    request: web.Request, handler: web.RequestHandler
) -> web.Response:
    if request.path == "/api/health":
        return await handler(request)
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or not hmac.compare_digest(
        auth[len("Bearer "):], API_KEY
    ):
        raise web.HTTPForbidden(reason="Invalid API key")
    return await handler(request)


# ---------------------------------------------------------------------------
# Borg helpers (run in executor so the event loop stays unblocked)
# ---------------------------------------------------------------------------


# SSH private key written by run.sh (if a remote repo needs one) and the
# known_hosts file borg/ssh should trust. Both live under /data so they
# survive add-on restarts; known_hosts accumulates host keys via
# accept-new instead of requiring an interactive/manual first connection.
SSH_KEY_PATH = Path("/root/.ssh/id_borg")
KNOWN_HOSTS_PATH = Path("/data/known_hosts")


def _borg_rsh() -> str:
    # A hanging SSH connection (dead peer, firewall black-hole) must not
    # block a request — or the add-on's own startup — forever.
    parts = [
        "ssh",
        "-o", "ConnectTimeout=30",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={KNOWN_HOSTS_PATH}",
    ]
    if SSH_KEY_PATH.exists():
        parts += ["-i", str(SSH_KEY_PATH)]
    return " ".join(parts)


def _borg_env() -> dict[str, str]:
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = PASSPHRASE
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    env["BORG_RSH"] = _borg_rsh()
    env.pop("BORG_REPO", None)  # we always pass it explicitly
    return env


async def _run_borg(
    *args: str, timeout: float | None = None
) -> subprocess.CompletedProcess:
    """Run a borg command in a thread-pool executor.

    Serialized through ``_BORG_LOCK`` so this add-on never fires two borg
    commands at the same repository concurrently (borg's own lock is
    per-command and per-process, not a mutex the add-on shares across
    requests), and given a generous ``--lock-wait`` so a command issued while
    a multi-minute upload holds the repo lock waits it out instead of failing.

    ``timeout`` bounds commands that should always be quick (`list`, `info`)
    so a stuck SSH session can't wedge the request handling it; long-running
    commands (`create`, `extract`, `prune`, `compact`) intentionally pass
    none.
    """
    loop = asyncio.get_running_loop()
    async with _BORG_LOCK:
        return await loop.run_in_executor(
            None,
            lambda: subprocess.run(
                ["borg", "--lock-wait", LOCK_WAIT, *args],
                check=True,
                capture_output=True,
                env=_borg_env(),
                timeout=timeout,
            ),
        )


def _ensure_repo() -> None:
    """Create the Borg repository on first run if it does not exist yet.

    Controlled by the ``init_if_not_exists`` add-on option. This only inits when
    borg reports the repository is genuinely missing — a passphrase or
    connectivity error is surfaced rather than being silently "fixed" by
    re-initialising over a repo we simply failed to open. Runs synchronously at
    startup, before the event loop starts.
    """
    if not INIT_IF_NOT_EXISTS:
        return
    probe = subprocess.run(
        ["borg", "info", REPO_PATH],
        check=False,
        capture_output=True,
        text=True,
        env=_borg_env(),
        timeout=QUICK_TIMEOUT,
    )
    if probe.returncode == 0:
        return
    stderr = (probe.stderr or "").strip()
    stderr_lower = stderr.lower()
    # Borg reports a genuinely missing path as "does not exist", but a path
    # that exists as an empty/uninitialised directory — as some hosted repo
    # providers (e.g. BorgBase) pre-provision — as "is not a valid
    # repository". Both mean "needs init"; anything else (wrong passphrase,
    # connectivity) must not be auto-"fixed" by re-initialising over it.
    if "does not exist" not in stderr_lower and "is not a valid repository" not in stderr_lower:
        _LOG.warning(
            "Could not verify repository at %s; not auto-initialising: %s",
            REPO_PATH,
            stderr,
        )
        return
    encryption = "repokey" if PASSPHRASE else "none"
    if not PASSPHRASE:
        _LOG.warning(
            "No passphrase set; initialising %s UNENCRYPTED. Set a passphrase "
            "before the first backup if this is not intentional — it cannot "
            "be added to an existing unencrypted repo later.",
            REPO_PATH,
        )
    _LOG.info(
        "Repository not found at %s; initialising (encryption=%s)",
        REPO_PATH,
        encryption,
    )
    init = subprocess.run(
        ["borg", "init", "--encryption", encryption, REPO_PATH],
        check=False,
        capture_output=True,
        text=True,
        env=_borg_env(),
        timeout=QUICK_TIMEOUT,
    )
    if init.returncode == 0:
        _LOG.info("Repository initialised at %s", REPO_PATH)
    else:
        _LOG.error("borg init failed: %s", (init.stderr or "").strip())


def _is_missing_archive_error(exc: subprocess.CalledProcessError) -> bool:
    """True if a failed borg command failed because the archive doesn't exist.

    Any other failure (lock timeout, repo unreachable, wrong passphrase, ...)
    must not be reported to HA as a 404 — that would look like the backup was
    deleted rather than like the underlying error it actually is.
    """
    stderr = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(
        exc.stderr, bytes
    ) else (exc.stderr or "")
    return "does not exist" in stderr.lower()


async def _list_archives() -> list[dict]:
    """Return list of HA backup metadata dicts from the repository.

    A single `borg list --json --format '{comment}'` call reads every
    archive's comment in one pass: `--format` is otherwise ignored under
    `--json`, but any key it references (here `comment`) is added to each
    archive's JSON object. This avoids one `borg info` round-trip per
    archive, which for a remote/SSH repo meant a new connection and lock
    acquisition per backup.
    """
    result = await _run_borg(
        "list",
        "--json",
        "--format", "{comment}",
        "--glob-archives", f"{ARCHIVE_PREFIX}*",
        REPO_PATH,
        timeout=QUICK_TIMEOUT,
    )
    data = json.loads(result.stdout)
    backups = []
    for archive in data.get("archives", []):
        comment = archive.get("comment", "")
        if not comment:
            continue
        try:
            meta = json.loads(_decode_comment(comment))
        except (json.JSONDecodeError, TypeError) as exc:
            _LOG.warning("Skipping archive %s: %s", archive["name"], exc)
            continue
        backups.append(meta)
    return backups


async def _prune() -> None:
    """Prune old archives based on retention configuration."""
    if KEEP_DAILY == 0 and KEEP_WEEKLY == 0 and KEEP_MONTHLY == 0:
        return
    prune_args = [
        "prune",
        "--glob-archives", f"{ARCHIVE_PREFIX}*",
        "--keep-daily", str(KEEP_DAILY),
        "--keep-weekly", str(KEEP_WEEKLY),
        "--keep-monthly", str(KEEP_MONTHLY),
        REPO_PATH,
    ]
    await _run_borg(*prune_args)
    try:
        await _run_borg("compact", REPO_PATH)
    except subprocess.CalledProcessError as exc:
        # Since borg 1.2, prune only marks segments deletable; compact is what
        # actually reclaims the disk space. A failed compact isn't fatal —
        # the prune itself already succeeded — so only log it.
        _LOG.error("borg compact failed after prune: %s", exc.stderr)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def health(request: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def list_backups(request: web.Request) -> web.Response:
    try:
        backups = await _list_archives()
        return web.json_response(backups)
    except subprocess.CalledProcessError as exc:
        _LOG.error("borg list failed: %s", exc.stderr)
        raise web.HTTPInternalServerError(reason="borg list failed") from exc


async def upload_backup(request: web.Request) -> web.Response:
    backup_id = request.headers.get("X-Backup-Id")
    backup_meta_json = request.headers.get("X-Backup-Meta")

    if not backup_id or not backup_meta_json:
        raise web.HTTPBadRequest(reason="X-Backup-Id and X-Backup-Meta headers required")
    if not BACKUP_ID_RE.match(backup_id):
        raise web.HTTPBadRequest(reason="Invalid X-Backup-Id")

    archive_name = f"{ARCHIVE_PREFIX}{backup_id}"

    # Stream the request body (tar) straight into borg's stdin: no on-disk
    # copy, so the add-on never needs double the backup's size in free space.
    async with _BORG_LOCK:
        proc = await asyncio.create_subprocess_exec(
            "borg", "create",
            "--lock-wait", LOCK_WAIT,
            "--stdin-name", f"{backup_id}.tar",
            "--comment", _encode_comment(backup_meta_json),
            f"{REPO_PATH}::{archive_name}",
            "-",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_borg_env(),
        )
        assert proc.stdin is not None
        assert proc.stdout is not None
        assert proc.stderr is not None

        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        async def _drain(stream: asyncio.StreamReader, sink: list[bytes]) -> None:
            while True:
                data = await stream.read(CHUNK_SIZE)
                if not data:
                    break
                sink.append(data)

        drain_tasks = [
            asyncio.create_task(_drain(proc.stdout, stdout_chunks)),
            asyncio.create_task(_drain(proc.stderr, stderr_chunks)),
        ]

        client_gone: BaseException | None = None
        try:
            async for chunk in request.content.iter_chunked(CHUNK_SIZE):
                proc.stdin.write(chunk)
                await proc.stdin.drain()
        except (ConnectionError, asyncio.CancelledError) as exc:
            client_gone = exc
            proc.terminate()
        finally:
            if client_gone is None:
                proc.stdin.close()
            returncode = await proc.wait()
            await asyncio.gather(*drain_tasks)

    # _run_borg() also takes _BORG_LOCK, so any cleanup that uses it must run
    # after the `async with` block above releases it (the lock isn't
    # reentrant).
    if client_gone is not None:
        _LOG.warning("Upload of %s aborted by client", backup_id)
        # borg does not create the target archive on an aborted `create`,
        # but a checkpoint archive may have been written; remove it so it
        # doesn't linger as a partial, unlisted archive.
        try:
            await _run_borg(
                "delete", "--glob-archives", f"{archive_name}.checkpoint*",
                REPO_PATH,
            )
        except subprocess.CalledProcessError:
            pass
        raise web.HTTPInternalServerError(reason="Upload aborted") from client_gone

    if returncode != 0:
        stderr_text = b"".join(stderr_chunks).decode("utf-8", "replace").strip()
        _LOG.error("borg create failed: %s", stderr_text)
        raise web.HTTPInternalServerError(reason="borg create failed")

    try:
        await _prune()
    except subprocess.CalledProcessError as exc:
        # The backup itself is safely stored at this point; a prune failure
        # must not be reported to HA as a failed backup.
        _LOG.error("borg prune failed after successful upload: %s", exc.stderr)

    return web.Response(status=201)


async def download_backup(request: web.Request) -> web.StreamResponse:
    backup_id = request.match_info["backup_id"]
    if not BACKUP_ID_RE.match(backup_id):
        raise web.HTTPBadRequest(reason="Invalid backup_id")
    archive_name = f"{ARCHIVE_PREFIX}{backup_id}"

    # Verify the archive exists first so a missing backup is a clean 404 rather
    # than a 200 with an empty/aborted body.
    try:
        await _run_borg("info", f"{REPO_PATH}::{archive_name}", timeout=QUICK_TIMEOUT)
    except subprocess.CalledProcessError as exc:
        if not _is_missing_archive_error(exc):
            _LOG.error("borg info failed: %s", exc.stderr)
            raise web.HTTPInternalServerError(reason="borg info failed") from exc
        raise web.HTTPNotFound(reason=f"Backup {backup_id} not found") from exc

    async with _BORG_LOCK:
        proc = await asyncio.create_subprocess_exec(
            "borg", "extract",
            "--lock-wait", LOCK_WAIT,
            "--stdout", f"{REPO_PATH}::{archive_name}",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_borg_env(),
        )
        assert proc.stdout is not None
        assert proc.stderr is not None

        # Drain stderr concurrently: borg can emit progress/warnings there, and if we
        # only ever read stdout, a full stderr pipe (~64 KiB) would deadlock borg.
        stderr_chunks: list[bytes] = []

        async def _drain_stderr() -> None:
            while True:
                data = await proc.stderr.read(CHUNK_SIZE)
                if not data:
                    break
                stderr_chunks.append(data)

        stderr_task = asyncio.create_task(_drain_stderr())

        response = web.StreamResponse(
            status=200,
            headers={"Content-Type": "application/octet-stream"},
        )
        await response.prepare(request)

        client_gone: BaseException | None = None
        try:
            while True:
                chunk = await proc.stdout.read(CHUNK_SIZE)
                if not chunk:
                    break
                await response.write(chunk)
        except (ConnectionError, asyncio.CancelledError) as exc:
            # The client disconnected mid-download. Stop borg so it doesn't block
            # forever on a stdout pipe that no one is reading.
            client_gone = exc
            proc.terminate()
        finally:
            returncode = await proc.wait()
            await stderr_task

        if client_gone is not None:
            _LOG.warning("Download of %s aborted by client", backup_id)
            raise client_gone

        if returncode != 0:
            stderr_text = b"".join(stderr_chunks).decode("utf-8", "replace").strip()
            _LOG.error("borg extract failed (code %d): %s", returncode, stderr_text)
            # We already committed a 200 and streamed a partial, corrupt body. We
            # must NOT finish the chunked response cleanly, or the client would
            # accept the truncated data as a complete backup. Abort the connection
            # so the client sees an incomplete transfer and treats it as an error.
            transport = request.transport
            if transport is not None:
                transport.close()
            raise web.HTTPInternalServerError(reason="borg extract failed")

        await response.write_eof()
        return response


async def delete_backup(request: web.Request) -> web.Response:
    backup_id = request.match_info["backup_id"]
    if not BACKUP_ID_RE.match(backup_id):
        raise web.HTTPBadRequest(reason="Invalid backup_id")
    archive_name = f"{ARCHIVE_PREFIX}{backup_id}"

    try:
        await _run_borg("info", f"{REPO_PATH}::{archive_name}", timeout=QUICK_TIMEOUT)
    except subprocess.CalledProcessError as exc:
        if not _is_missing_archive_error(exc):
            _LOG.error("borg info failed: %s", exc.stderr)
            raise web.HTTPInternalServerError(reason="borg info failed") from exc
        raise web.HTTPNotFound(reason=f"Backup {backup_id} not found") from exc

    try:
        await _run_borg("delete", f"{REPO_PATH}::{archive_name}")
    except subprocess.CalledProcessError as exc:
        _LOG.error("borg delete failed: %s", exc.stderr)
        raise web.HTTPInternalServerError(reason="borg delete failed") from exc

    try:
        await _run_borg("compact", REPO_PATH)
    except subprocess.CalledProcessError as exc:
        # The delete already succeeded; a failed compact just means the freed
        # space isn't reclaimed until the next successful prune/delete/compact.
        _LOG.error("borg compact failed after delete: %s", exc.stderr)

    return web.Response(status=204)


# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------


def create_app() -> web.Application:
    # aiohttp's default 8190-byte header field limit is too small for
    # X-Backup-Meta on an install with many add-ons/folders (the full
    # backup.as_dict() metadata); raise it well above what a realistic
    # HA metadata dict serializes to.
    app = web.Application(
        middlewares=[_auth_middleware],
        handler_args={"max_field_size": 65536},
    )
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/backups", list_backups)
    app.router.add_post("/api/backups", upload_backup)
    app.router.add_get("/api/backups/{backup_id}", download_backup)
    app.router.add_delete("/api/backups/{backup_id}", delete_backup)
    return app


if __name__ == "__main__":
    _ensure_repo()
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=8774)
