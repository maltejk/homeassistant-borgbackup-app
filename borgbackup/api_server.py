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
import json
import logging
import os
import subprocess
import tempfile
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

BACKUP_DIR = Path("/backup")
CHUNK_SIZE = 1024 * 1024  # 1 MiB streaming chunks
ARCHIVE_PREFIX = "ha-"


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
    if not auth.startswith("Bearer ") or auth[len("Bearer "):] != API_KEY:
        raise web.HTTPForbidden(reason="Invalid API key")
    return await handler(request)


# ---------------------------------------------------------------------------
# Borg helpers (run in executor so the event loop stays unblocked)
# ---------------------------------------------------------------------------


def _borg_env() -> dict[str, str]:
    env = os.environ.copy()
    env["BORG_PASSPHRASE"] = PASSPHRASE
    env["BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK"] = "yes"
    env.pop("BORG_REPO", None)  # we always pass it explicitly
    return env


async def _run_borg(*args: str) -> subprocess.CompletedProcess:
    """Run a borg command in a thread-pool executor."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: subprocess.run(
            ["borg", *args],
            check=True,
            capture_output=True,
            env=_borg_env(),
        ),
    )


async def _list_archives() -> list[dict]:
    """Return list of HA backup metadata dicts from the repository."""
    result = await _run_borg(
        "list",
        "--json",
        "--glob-archives", f"{ARCHIVE_PREFIX}*",
        REPO_PATH,
    )
    data = json.loads(result.stdout)
    backups = []
    for archive in data.get("archives", []):
        # Metadata is stored in the archive comment
        try:
            comment_result = await _run_borg(
                "info",
                "--json",
                f"{REPO_PATH}::{archive['name']}",
            )
            info = json.loads(comment_result.stdout)
            comment = info["archives"][0].get("comment", "")
            if comment:
                meta = json.loads(_decode_comment(comment))
                backups.append(meta)
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Skipping archive %s: %s", archive["name"], exc)
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

    archive_name = f"{ARCHIVE_PREFIX}{backup_id}"

    # Stream the request body (tar) into borg via stdin
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tar_path = Path(tmpdir) / f"{backup_id}.tar"
            with open(tar_path, "wb") as f:
                async for chunk in request.content.iter_chunked(CHUNK_SIZE):
                    f.write(chunk)

            # Create borg archive from the tar file using --stdin
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: subprocess.run(
                    [
                        "borg", "create",
                        "--stdin-name", f"{backup_id}.tar",
                        "--comment", _encode_comment(backup_meta_json),
                        f"{REPO_PATH}::{archive_name}",
                        "-",
                    ],
                    stdin=open(tar_path, "rb"),
                    check=True,
                    capture_output=True,
                    env=_borg_env(),
                ),
            )

        await _prune()

    except subprocess.CalledProcessError as exc:
        _LOG.error("borg create failed: %s", exc.stderr)
        raise web.HTTPInternalServerError(reason="borg create failed") from exc

    return web.Response(status=201)


async def download_backup(request: web.Request) -> web.StreamResponse:
    backup_id = request.match_info["backup_id"]
    archive_name = f"{ARCHIVE_PREFIX}{backup_id}"

    # Verify the archive exists first so a missing backup is a clean 404 rather
    # than a 200 with an empty/aborted body.
    try:
        await _run_borg("info", f"{REPO_PATH}::{archive_name}")
    except subprocess.CalledProcessError:
        raise web.HTTPNotFound(reason=f"Backup {backup_id} not found")

    proc = await asyncio.create_subprocess_exec(
        "borg", "extract", "--stdout", f"{REPO_PATH}::{archive_name}",
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
    archive_name = f"{ARCHIVE_PREFIX}{backup_id}"

    try:
        await _run_borg("info", f"{REPO_PATH}::{archive_name}")
    except subprocess.CalledProcessError:
        raise web.HTTPNotFound(reason=f"Backup {backup_id} not found")

    try:
        await _run_borg("delete", f"{REPO_PATH}::{archive_name}")
    except subprocess.CalledProcessError as exc:
        _LOG.error("borg delete failed: %s", exc.stderr)
        raise web.HTTPInternalServerError(reason="borg delete failed") from exc

    return web.Response(status=204)


# ---------------------------------------------------------------------------
# Application setup
# ---------------------------------------------------------------------------


def create_app() -> web.Application:
    app = web.Application(middlewares=[_auth_middleware])
    app.router.add_get("/api/health", health)
    app.router.add_get("/api/backups", list_backups)
    app.router.add_post("/api/backups", upload_backup)
    app.router.add_get("/api/backups/{backup_id}", download_backup)
    app.router.add_delete("/api/backups/{backup_id}", delete_backup)
    return app


if __name__ == "__main__":
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=8774)
