# BorgBackup Add-on

[![License](https://img.shields.io/github/license/maltejk/homeassistant-borgbackup-app)](LICENSE)

A Home Assistant add-on that runs a [Borg](https://www.borgbackup.org/) backup repository
manager. It exposes an HTTP API on port 8774 for the
[BorgBackup HA integration](https://github.com/maltejk/homeassistant-borgbackup-integration).

This is AI generated code. Please find the instructions in [copilot-instructions.md](.github/copilot-instructions.md).

---

## Features

- **HA integration bridge** — REST API for the BorgBackup integration to list, upload,
  download, and delete backups through Home Assistant's native Backup UI
- **SSH remote repositories** — supports `user@host:/path` repositories via private key
- **Optional extra retention** — Home Assistant manages backup retention itself (since
  2025.1); the add-on's own keep-daily / keep-weekly / keep-monthly pruning is off by
  default and only needed as an additional safety net
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
| `init_if_not_exists` | `true`      | Create the repo automatically on first start if it doesn't exist   |
| `keep_daily`       | `0`           | Daily archives to retain (0 = disabled; see note below)            |
| `keep_weekly`      | `0`           | Weekly archives to retain (0 = disabled; see note below)           |
| `keep_monthly`     | `0`           | Monthly archives to retain (0 = disabled; see note below)          |

> **Note:** Home Assistant manages backup retention itself (Settings → Backups →
> "Backups to keep") since 2025.1 and deletes old backups through this add-on when
> that limit is exceeded. The `keep_*` options are an **additional, optional** safety
> net on top of that — leave them at `0` unless you specifically want the add-on to
> also enforce its own limit (e.g. to protect against retention set to "forever" in
> HA). Running both with different limits means the stricter one wins for a given
> archive.

> **Note:** With `init_if_not_exists: true` (the default) the add-on initialises
> the repository automatically on first start — `repokey` encryption when a
> `passphrase` is set, otherwise unencrypted. Set it to `false` to manage the
> repo yourself:
> ```bash
> borg init --encryption=repokey user@host:/path/to/repo
> ```

---

## API

The add-on listens on port **8774**, reachable from Home Assistant (and other add-ons)
over the internal Supervisor network — the integration never needs the host port
mapping. It's disabled by default; enable it in the add-on's **Network** tab only if
something outside HA (e.g. an external Docker host) needs to reach the API directly,
and note that this exposes plaintext HTTP. All endpoints except `/api/health` require:

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

## Encryption: use Borg's, not Home Assistant's

Home Assistant encrypts backups for storage agents **by default**. An HA-encrypted tar
is opaque, high-entropy data to Borg — it can't deduplicate or meaningfully compress it,
which is most of the point of using Borg as a backend. For this destination, disable HA's
own backup encryption and rely on Borg's `repokey` encryption (set a `passphrase` above)
instead: same protection, but Borg can still dedupe/compress across archives.

Whichever encryption you use, back up the key itself:
```bash
borg key export REPOSITORY encrypted-key-backup
```
Without it, a lost/corrupted repo's `repokey` (stored inside the repo) or a lost HA
encryption password makes the backups permanently unreadable, encryption working exactly
as intended.

---

## Troubleshooting

**Add-on won't start** — `repo_path` is required; the add-on refuses to start without
it. For SSH repos, verify that the SSH host is reachable from the add-on network; a
first-time connection to a new host is accepted and its key remembered automatically
(no manual `known_hosts` step needed).

**Integration shows "cannot connect"** — Confirm the add-on is running (`Started`) and
that HA can reach port 8774. Check the add-on network configuration.

**"Invalid API key"** — Find the current key in the add-on log at startup, or read
`/data/api_key` inside the container.

**Borg error during upload** — The repository is auto-initialised on first start
when `init_if_not_exists` is enabled. If you disabled it, run `borg init`
manually. Otherwise check the add-on log for the full borg error message.

---

## License

[MIT](LICENSE)
