# BorgBackup Add-on

[![License](https://img.shields.io/github/license/maltejk/homeassistant-borgbackup-app)](LICENSE)

A Home Assistant add-on that runs a [Borg](https://www.borgbackup.org/) backup repository
manager. It exposes an HTTP API on port 8774 for the
[BorgBackup HA integration](https://github.com/maltejk/homeassistant-borgbackup-integration), and
optionally runs [borgmatic](https://torsion.org/borgmatic/) on a cron schedule for
standalone encrypted, deduplicated backups independent of HA.

This is AI generated code. Please find the instructions in [copilot-instructions.md](.github/copilot-instructions.md).

---

## Features

- **HA integration bridge** — REST API for the BorgBackup integration to list, upload,
  download, and delete backups through Home Assistant's native Backup UI
- **Standalone scheduling** — optional cron-based borgmatic runs independent of HA
- **SSH remote repositories** — supports `user@host:/path` repositories via private key
- **Retention policies** — configurable keep-daily / keep-weekly / keep-monthly pruning
  applied automatically after every upload
- **Encrypted** — full AES-256 encryption via Borg's native `repokey` mode
- **Auto-generated API key** — generated on first start, never hardcoded

---

## Installation

1. Add this repository to your Home Assistant add-on store:
   ```
   https://github.com/maltejk/homeassistant-borgbackup-app
   ```
2. Install the **BorgBackup** add-on.
3. Configure the add-on (see [Configuration](#configuration) below).
4. Start the add-on — the API key is printed in the add-on log.
5. Install the [BorgBackup HA integration](https://github.com/maltejk/homeassistant-borgbackup-integration).
   If running as an HA add-on, it will be auto-discovered by Supervisor.

---

## Configuration

| Option             | Default       | Description                                                        |
|--------------------|---------------|--------------------------------------------------------------------|
| `repo_path`        | *(required)*  | Borg repo path: local `/mnt/borg` or SSH `user@host:/path/to/repo` |
| `passphrase`       | `""`          | Repo encryption passphrase                                         |
| `ssh_private_key`  | `""`          | Full content of the SSH private key (PEM format) for remote repos  |
| `keep_daily`       | `7`           | Daily archives to retain (0 = disabled)                            |
| `keep_weekly`      | `4`           | Weekly archives to retain (0 = disabled)                           |
| `keep_monthly`     | `6`           | Monthly archives to retain (0 = disabled)                          |
| `schedule_enabled` | `false`       | Enable standalone borgmatic cron schedule                          |
| `schedule_cron`    | `"0 3 * * *"` | Cron expression for scheduled borgmatic runs                       |

> **Note:** The Borg repository must be initialised before use:
> ```bash
> borg init --encryption=repokey user@host:/path/to/repo
> ```

---

## API

The add-on listens on port **8774**. All endpoints except `/api/health` require:

```
Authorization: Bearer <api_key>
```

The API key is auto-generated on first start and stored at `/data/api_key` inside the
container. It is printed in the add-on log on startup.

| Method   | Path                       | Description              | Success   |
|----------|----------------------------|--------------------------|-----------|
| `GET`    | `/api/health`              | Health check             | 200       |
| `GET`    | `/api/backups`             | List all HA backups      | 200       |
| `POST`   | `/api/backups`             | Upload a backup archive  | 201       |
| `GET`    | `/api/backups/{backup_id}` | Download a backup        | 200       |
| `DELETE` | `/api/backups/{backup_id}` | Delete a backup          | 204       |

**Upload headers:**

| Header         | Value                                              |
|----------------|----------------------------------------------------|
| `X-Backup-Id`  | The HA backup ID                                   |
| `X-Backup-Meta`| JSON-encoded `backup.as_dict()` metadata           |

---

## How backups are stored

Each backup is a Borg archive named `ha-<backup-id>`. The full HA metadata dict is
stored as JSON in the archive **comment** — no extra files in the repository, and no
additional storage overhead beyond the backup data itself.

---

## Standalone scheduling

Set `schedule_enabled: true` and a cron expression in `schedule_cron` to have bormatic
run independently of Home Assistant. This is complementary to HA-triggered backups:

- HA-triggered backups: started from the HA Backup UI, streamed to this add-on
- Standalone backups: run by borgmatic on schedule, back up `/backup` directly

---

## Troubleshooting

**Add-on won't start** — Check that `repo_path` is set. For SSH repos, verify that the
SSH host is reachable from the add-on network.

**Integration shows "cannot connect"** — Confirm the add-on is running (`Started`) and
that HA can reach port 8774. Check the add-on network configuration.

**"Invalid API key"** — Find the current key in the add-on log at startup, or read
`/data/api_key` inside the container.

**Borg error during upload** — The repository must exist before first use. Run
`borg init` manually or check the add-on log for the full borg error message.

---

## License

[MIT](LICENSE)


## Features

- **HA integration bridge**: exposes a REST API so the BorgBackup integration can list,
  upload, download, and delete backups through Home Assistant's native backup UI
- **Standalone scheduling**: optional cron-based borgmatic runs independent of HA
- **SSH remote repositories**: supports `user@host:/path` repositories via SSH key
- **Retention policies**: configurable keep-daily / keep-weekly / keep-monthly pruning
- **Encrypted**: full AES-256 encryption via Borg native encryption

## Installation

1. Add this repository to your Home Assistant add-on store:
   ```
   https://github.com/maltejk/homeassistant-borgbackup-app
   ```
2. Install the **BorgBackup** add-on.
3. Configure the add-on (see Configuration below).
4. Start the add-on.
5. Install the [BorgBackup integration](https://github.com/maltejk/homeassistant-borgbackup-integration)
   — it will auto-discover the add-on via Supervisor.

## Configuration

| Option             | Default        | Description                                          |
|--------------------|----------------|------------------------------------------------------|
| `repo_path`        | (required)     | Borg repo path (local `/mnt/borg` or SSH `user@host:path`) |
| `passphrase`       | `""`           | Repo encryption passphrase                           |
| `ssh_private_key`  | `""`           | SSH private key contents for remote repos            |
| `keep_daily`       | `7`            | Daily archives to retain (0 = disabled)              |
| `keep_weekly`      | `4`            | Weekly archives to retain (0 = disabled)             |
| `keep_monthly`     | `6`            | Monthly archives to retain (0 = disabled)            |
| `schedule_enabled` | `false`        | Enable standalone borgmatic cron schedule            |
| `schedule_cron`    | `"0 3 * * *"`  | Cron expression for scheduled borgmatic runs         |

## API

The add-on exposes an HTTP API on port **8774**. All endpoints (except `/api/health`)
require a `Authorization: Bearer <api_key>` header. The API key is auto-generated on
first start and displayed in the add-on log.

| Method   | Path                        | Description              |
|----------|-----------------------------|--------------------------|
| `GET`    | `/api/health`               | Health check             |
| `GET`    | `/api/backups`              | List all HA backups      |
| `POST`   | `/api/backups`              | Upload a backup archive  |
| `GET`    | `/api/backups/{backup_id}`  | Download a backup        |
| `DELETE` | `/api/backups/{backup_id}`  | Delete a backup          |

## Notes

- The add-on and integration run two complementary modes: the **integration** triggers
  backups via Home Assistant's Backup UI; **standalone scheduling** runs borgmatic
  independently on a cron timer for extra safety.
- The Borg repository must be initialised before use. Run:
  ```
  borg init --encryption=repokey <repo_path>
  ```
  or enable `init_if_not_exists` (planned feature).
