# borgbackup-addon — Copilot Instructions

borgbackup-addon is a Home Assistant add-on that wraps the `borg` CLI (Alpine `borgbackup`
package) with an aiohttp HTTP API server. The integration in `HomeassistantBorgBackup`
exclusively depends on this add-on; the add-on has no direct dependency on the integration.
There is no `borgmatic` and no cron/standalone scheduling — HA is the only thing that
triggers a backup; see [CHANGELOG.md](../CHANGELOG.md) for when that was removed.

## Architecture

```
borgbackup/
  Dockerfile          # FROM ghcr.io/hassio-addons/base; apk add borgbackup + aiohttp
  build.yaml          # Pins BUILD_FROM per architecture
  config.yaml         # HA Supervisor add-on metadata and options schema
  run.sh              # Entry point: generates API key, publishes discovery, writes
                      # the SSH key (if configured), launches api_server.py
  api_server.py       # aiohttp REST server on port 8774
  translations/
    en.yaml           # Supervisor UI option descriptions
repository.yaml       # HACS/Supervisor repository metadata
```

**Runtime model:** `run.sh` is the container entry point. It generates a random bearer
token (stored at `/data/api_key`), publishes Supervisor discovery, writes the user's SSH
private key to disk if one was configured, and then launches `api_server.py`.

**`api_server.py`** is a plain `aiohttp` application. All borg operations are executed via `subprocess` in an `asyncio` executor so the event loop is never blocked.

## API Endpoints

| Method | Path | Success | Description |
|--------|------|---------|-------------|
| `GET`  | `/api/health` | 200 | Health check (no auth required) |
| `GET`  | `/api/backups` | 200 | List all `ha-` prefixed archives (`[{backup_id, meta}, ...]`) |
| `POST` | `/api/backups` | 201 | Upload a backup tar (stream body; `X-Backup-Id`, `X-Backup-Meta` headers required) |
| `GET`  | `/api/backups/{backup_id}` | 200 | Stream the tar body for a backup |
| `DELETE` | `/api/backups/{backup_id}` | 204 | Delete an archive, then `borg compact` |

Authentication: `Authorization: Bearer <api_key>` on all endpoints except `/api/health`.

Archive naming: all HA-managed archives use the prefix `ha-` (`ARCHIVE_PREFIX`). This separates them from any other archives a user creates in the same repository with the plain `borg` CLI.

## Key Conventions

- **Auth middleware** (`_auth_middleware`): checks the `Authorization` header for all routes except `/api/health`. Returns HTTP 403 on failure — never 401, to avoid browser basic-auth prompts. Compares the token with `hmac.compare_digest`, never `==`.
- **Subprocess isolation:** all `borg` calls use `subprocess.run()` (or `asyncio.create_subprocess_exec()` for the streaming create/extract) wrapped in `asyncio.get_running_loop().run_in_executor()` / awaited directly. Never call blocking code directly in `async` handlers.
- **Serialization:** every borg invocation goes through `_run_borg()` or is wrapped in `_BORG_LOCK` directly (streaming create/extract) — this add-on never runs two borg commands against the repo concurrently, and passes a generous `--lock-wait` (`LOCK_WAIT`) so a command issued mid-upload waits instead of failing.
- **Environment-driven config:** all runtime settings come from environment variables set by `run.sh` — `BORGBACKUP_API_KEY`, `BORGBACKUP_REPO_PATH`, `BORGBACKUP_PASSPHRASE`, `BORGBACKUP_KEEP_DAILY`, `BORGBACKUP_KEEP_WEEKLY`, `BORGBACKUP_KEEP_MONTHLY`. Do not hard-code paths or credentials.
- **Retention is opt-in:** HA manages backup retention itself (2025.1+); `_prune()` is a no-op unless a user explicitly sets a non-zero `keep_*` option (default `0`/`0`/`0`). `borg compact` always follows a successful `prune` or `delete` (borg only reclaims space on `compact`, not `prune`/`delete` themselves) — a failed `compact`/`prune` after an already-successful upload/delete is logged, never turned into an HTTP error.
- **Streaming:** both upload and download pipe directly to/from a `borg create`/`borg extract` subprocess's stdin/stdout (`CHUNK_SIZE = 1 MiB` chunks) — no on-disk staging copy, so the add-on never needs double the backup's size in free space.
- **Archive metadata:** HA backup metadata (`X-Backup-Meta` JSON) is stored as the Borg archive **comment** (`--comment` on create). `GET /api/backups` reads every archive's comment in a single `borg list --json --format '{comment}'` call — do not reintroduce a per-archive `borg info` call here, it was an N+1 pattern that also caused sporadic missing backups (a lock-timeout on any single `info` call silently dropped that archive from the list).
- **backup_id validation:** `BACKUP_ID_RE` (`^[A-Za-z0-9_-]{1,64}$`) gates every endpoint that takes a `backup_id` — it becomes part of an archive name and was a path-traversal vector before validation was added.
- Keep add-on options in `config.yaml`. Add `en.yaml` descriptions for any new option.

## Add-on Options

See [README.md — Configuration](../README.md#configuration) for the full options table. When adding a new option, update both `config.yaml` and `translations/en.yaml` together.

## Build & CI

```bash
# Validate Docker build only (no unit tests for the add-on itself)
docker build -f borgbackup/Dockerfile borgbackup/
```

CI (`.github/workflows/ci.yml`) runs a Docker build validation on push/PR.

## Pitfalls

- `/api/health` must **not** require authentication — the integration probes it before credentials are known.
- The API key file at `/data/api_key` persists across restarts (generated only if absent). Do not regenerate on every start.
- `BORG_PASSPHRASE` must be set in the subprocess environment via `_borg_env()` — never pass it as a CLI argument.
- `BORG_UNKNOWN_UNENCRYPTED_REPO_ACCESS_IS_OK=yes` must be set when the repo has no passphrase, otherwise borg refuses to open it.
- When adding new borg commands, always use `REPO_PATH` explicitly rather than relying on the `BORG_REPO` env var (it is explicitly removed in `_borg_env()` to avoid conflicts).
- SSH remote repos: `BORG_RSH` (`_borg_rsh()`) sets `ConnectTimeout`/`ServerAliveInterval` so a dead peer can't hang a request forever, plus `StrictHostKeyChecking=accept-new` against a `UserKnownHostsFile` persisted at `/data/known_hosts` — there is no interactive prompt available in a container, so without this a new host would otherwise fail closed. The private key (if configured) is written by `run.sh` to `/root/.ssh/id_borg`.

## Reference Docs

- [README.md](../README.md) — user-facing setup, config table, API reference, troubleshooting
- [CHANGELOG.md](../CHANGELOG.md) — version history
