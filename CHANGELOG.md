# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.0] - 2026-09-22

### Removed

- **Standalone borgmatic scheduling** (`schedule_enabled`, `schedule_cron` options)
  — it never worked (BusyBox `crond` doesn't read `/etc/cron.d`, the source
  directory didn't exist, the config format is deprecated in borgmatic 2.0, the
  passphrase wasn't YAML-quoted, and its archive-name-format collided with the
  add-on's own prune glob). Home Assistant is the only supported way to trigger
  a backup now; `borgmatic` was also dropped from the image.
- Unsupported architectures `armhf`, `armv7`, `i386` — no longer built by Home
  Assistant.

### Changed (breaking)

- `keep_daily`/`keep_weekly`/`keep_monthly` now default to `0` (disabled).
  Home Assistant has managed backup retention itself since 2025.1; the add-on's
  own pruning was fighting HA's own deletion and is now an explicit opt-in
  safety net instead of a silent default.
- Port `8774` is no longer exposed on the host by default — the integration
  reaches the add-on over the internal Supervisor network. Enable it manually
  if something outside HA needs direct access.
- `repo_path` has no default and startup now aborts with a clear error
  (`bashio::config.require`) if it's unset, instead of starting with a broken
  configuration.
- SSH private key is now written to `/root/.ssh/id_borg` (was `id_rsa`).

### Fixed

- **Upload no longer buffers the whole backup to disk first** — streams
  directly into `borg create`'s stdin, halving the disk space a backup needs
  and removing a leaked file descriptor and a synchronous write on the event
  loop.
- **`borg compact` now runs after prune/delete** — since borg 1.2, `prune`/
  `delete` alone don't reclaim disk space; the repository grew unbounded
  without it.
- **Discovery hostname** — uses `bashio::addon.hostname` instead of a
  hardcoded `local-borgbackup`, which only happens to be correct for a
  locally-installed add-on (an add-on installed from a repository has a
  different real hostname, and discovery couldn't be resolved).
- **Concurrent requests no longer fail on the repo lock** — all borg calls
  now share a generous `--lock-wait 600` and an in-process lock, instead of
  borg's 1-second default lock-wait causing spurious failures while an
  upload is running.
- **Backup listing no longer does N+1 `borg info` calls** (and no longer
  silently drops an archive from the list if one of those calls failed) —
  a single `borg list --json --format '{comment}'` call now reads every
  archive's metadata.
- **404 is now only returned when the archive is actually missing** — any
  other borg failure (lock timeout, unreachable repo) is a 500 instead of
  being misreported as "not found".
- `backup_id` is validated against a strict charset before it can reach a
  borg archive name (previously a path-traversal vector).
- API key comparison uses a constant-time comparison (`hmac.compare_digest`).
- `list`/`info`/SSH calls now have bounded timeouts, and SSH sets
  `StrictHostKeyChecking=accept-new` against a known_hosts file persisted at
  `/data/known_hosts` — a container has no interactive prompt to accept a new
  host key otherwise, so a fresh remote repo would previously fail closed.
- The 8190-byte header limit no longer 400s a large `X-Backup-Meta` (raised
  to 65536 bytes).
- Initialising a repository with no passphrase now logs a warning that it
  will be unencrypted.
- The API key is now only printed in full once, at generation time, instead
  of in plaintext on every restart.

### Added

- CI now lints `api_server.py` (py_compile + ruff) and `run.sh` (shellcheck),
  not just the Docker build.
- Add-on README: "Encryption" section explaining why Home Assistant's own
  backup encryption should be disabled for this destination in favor of
  Borg's `repokey`.

## [1.1.0] - 2026-07-09

### Added

- **Repository auto-init** — new `init_if_not_exists` option (default `true`):
  the add-on creates the Borg repository on first start (`repokey` when a
  `passphrase` is set, otherwise unencrypted). It only initialises when borg
  reports the repo is genuinely missing, so an existing repo that fails to open
  (wrong passphrase, unreachable host) is never clobbered.
- **Supervisor discovery** — now actually published: `hassio_api` is enabled and
  a `borgbackup` discovery message (host/port/api_key) is POSTed to the
  Supervisor on startup so the HA integration is offered for one-click setup.

### Fixed

- **Add-on could not build** — the Dockerfile referenced the nonexistent
  `borgmatic/borgmatic:latest`. Rebased onto the official hass.io add-on base
  image with `borgbackup`, `borgmatic`, `openssh-client` and `curl` from apk,
  plus a `build.yaml` pinning the base per architecture.
- **Every upload failed** — JSON backup metadata stored in the archive comment
  hit borg's placeholder engine (`Invalid placeholder`). Metadata is now
  base64-encoded on write and decoded on read.
- **Download robustness** — borg's stderr is drained concurrently (a full stderr
  pipe could deadlock the extract), borg is stopped if the client disconnects,
  and on a borg failure mid-stream the connection is aborted instead of finishing
  the chunked body, so a truncated download is never accepted as a complete backup.
- **Startup crash** — API-key generation used a shell pipeline that could abort
  `run.sh` with SIGPIPE under `pipefail`; replaced with a single `python3`
  `secrets.token_urlsafe` call. The key is now logged for manual setup.

### Changed

- Base image is now `ghcr.io/hassio-addons/base` (was `borgmatic/borgmatic`).

## [1.0.0] - 2026-04-06

### 🎉 Initial release

#### Features

- **HTTP API server** (`api_server.py`) on port 8774 with five endpoints:
  `GET /api/health`, `GET /api/backups`, `POST /api/backups`,
  `GET /api/backups/{id}`, `DELETE /api/backups/{id}`
- **Bearer token auth** — API key auto-generated on first start, stored at `/data/api_key`
- **Streaming upload** — backup tar body streamed from HA, piped into `borg create --stdin`
- **Streaming download** — `borg extract --stdout` streamed back to HA chunk by chunk
- **Automatic pruning** — configurable keep-daily / keep-weekly / keep-monthly applied after every upload
- **Standalone scheduling** — optional borgmatic cron schedule via `schedule_enabled` + `schedule_cron` options
- **SSH key support** — private key written to `/root/.ssh/id_rsa` (mode 0600) from add-on options
- **Borgmatic config** — generated from add-on options at container start
- **Supervisor discovery** — publishes connection details so the HA integration can auto-configure

#### Docker image

- Base image: `borgmatic/borgmatic:latest` (includes `borgmatic` + `borg`)
- Additional dependency: `aiohttp` (installed via pip)
