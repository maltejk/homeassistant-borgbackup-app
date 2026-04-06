# borgbackup-addon — Copilot Instructions

borgbackup-addon is a Home Assistant add-on that runs BorgBackup (via the `borgmatic` Docker image) alongside an aiohttp HTTP API server. The integration in `HomeassistantBorgBackup` exclusively depends on this add-on; the add-on has no direct dependency on the integration.

## Architecture

```
borgbackup/
  Dockerfile          # FROM borgmatic/borgmatic:latest; pip install aiohttp
  config.yaml         # HA Supervisor add-on metadata and options schema
  run.sh              # Entry point: generates API key, writes SSH key + borgmatic
                      # config, optionally starts crond, launches api_server.py
  api_server.py       # aiohttp REST server on port 8774
  translations/
    en.yaml           # Supervisor UI option descriptions
repository.yaml       # HACS/Supervisor repository metadata
```

**Runtime model:** `run.sh` is the container entry point. It generates a random bearer token (stored at `/data/api_key`), writes the user's SSH private key to disk, generates a `borgmatic` config, optionally starts `crond` for scheduled backups, and then launches `api_server.py`.

**`api_server.py`** is a plain `aiohttp` application. All borg operations are executed via `subprocess` in an `asyncio` executor so the event loop is never blocked.

## API Endpoints

| Method | Path | Success | Description |
|--------|------|---------|-------------|
| `GET`  | `/api/health` | 200 | Health check (no auth required) |
| `GET`  | `/api/backups` | 200 | List all `ha-` prefixed archives (`[{backup_id, meta}, ...]`) |
| `POST` | `/api/backups` | 201 | Upload a backup tar (stream body; `X-Backup-Id`, `X-Backup-Meta` headers required) |
| `GET`  | `/api/backups/{backup_id}` | 200 | Stream the tar body for a backup |
| `DELETE` | `/api/backups/{backup_id}` | 204 | Delete an archive and prune |

Authentication: `Authorization: Bearer <api_key>` on all endpoints except `/api/health`.

Archive naming: all HA-managed archives use the prefix `ha-` (`ARCHIVE_PREFIX`). This separates them from other borgmatic archives in the same repository.

## Key Conventions

- **Auth middleware** (`_auth_middleware`): checks the `Authorization` header for all routes except `/api/health`. Returns HTTP 403 on failure — never 401, to avoid browser basic-auth prompts.
- **Subprocess isolation:** all `borg` and `borgmatic` calls use `subprocess.run()` (or `Popen`) wrapped in `asyncio.get_running_loop().run_in_executor()`. Never call blocking code directly in `async` handlers.
- **Environment-driven config:** all runtime settings come from environment variables set by `run.sh` — `BORGBACKUP_API_KEY`, `BORGBACKUP_REPO_PATH`, `BORGBACKUP_PASSPHRASE`, `BORGBACKUP_KEEP_DAILY`, `BORGBACKUP_KEEP_WEEKLY`, `BORGBACKUP_KEEP_MONTHLY`. Do not hard-code paths or credentials.
- **Pruning:** runs automatically after every successful upload (inside the `POST /api/backups` handler).
- **Streaming:** both upload and download use chunked streaming (`CHUNK_SIZE = 1 MiB`) to handle large backup tars without buffering the whole file in memory.
- **Upload tempfile:** `POST /api/backups` streams the request body to a `tempfile.TemporaryDirectory` first, then calls `borg create --stdin-name ... -` reading from that file. Pure piped stdin is not used.
- **Archive metadata:** HA backup metadata (`X-Backup-Meta` JSON) is stored as the Borg archive **comment** (`--comment` on create, read back via `borg info --json`). No extra files in the repository.
- **`BACKUP_DIR`:** the borg source directory is `/backup` (constant in `api_server.py`). HA writes backup tars here; borg archives from this path.
- **`list_backups` N+1 pattern:** `GET /api/backups` does 1 `borg list` call then 1 `borg info` per archive to read comments. Avoid patterns that increase this further.
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

## Reference Docs

- [README.md](../README.md) — user-facing setup, config table, API reference, troubleshooting
- [CHANGELOG.md](../CHANGELOG.md) — version history
