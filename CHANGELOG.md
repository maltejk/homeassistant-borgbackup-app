# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
