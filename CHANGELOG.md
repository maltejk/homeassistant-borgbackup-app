# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
