#!/usr/bin/with-contenv bashio
set -e

API_KEY_FILE=/data/api_key

# ---------------------------------------------------------------------------
# Generate API key on first run
# ---------------------------------------------------------------------------
if [ ! -f "$API_KEY_FILE" ]; then
    bashio::log.info "Generating new API key..."
    API_KEY=$(cat /dev/urandom | tr -dc 'a-zA-Z0-9' | fold -w 40 | head -n 1)
    echo "$API_KEY" > "$API_KEY_FILE"
    chmod 600 "$API_KEY_FILE"
fi

API_KEY=$(cat "$API_KEY_FILE")

# ---------------------------------------------------------------------------
# Write borgmatic config from add-on options
# ---------------------------------------------------------------------------
REPO_PATH=$(bashio::config 'repo_path')
PASSPHRASE=$(bashio::config 'passphrase')
SSH_PRIVATE_KEY=$(bashio::config 'ssh_private_key')
KEEP_DAILY=$(bashio::config 'keep_daily')
KEEP_WEEKLY=$(bashio::config 'keep_weekly')
KEEP_MONTHLY=$(bashio::config 'keep_monthly')

mkdir -p /root/.config/borgmatic /root/.ssh
chmod 700 /root/.ssh

# Write SSH private key if provided
if [ -n "$SSH_PRIVATE_KEY" ]; then
    echo "$SSH_PRIVATE_KEY" > /root/.ssh/id_rsa
    chmod 600 /root/.ssh/id_rsa
fi

# Write borgmatic config
cat > /root/.config/borgmatic/config.yaml << BORGMATIC_EOF
location:
  repositories:
    - path: ${REPO_PATH}
      label: homeassistant
  source_directories:
    - /backup

storage:
  encryption_passphrase: ${PASSPHRASE}
  archive_name_format: ha-{now}
  borgmatic_source_directory: /var/borgmatic

retention:
  keep_daily: ${KEEP_DAILY}
  keep_weekly: ${KEEP_WEEKLY}
  keep_monthly: ${KEEP_MONTHLY}
  prefix: ha-

hooks:
  healthchecks: null
BORGMATIC_EOF

# ---------------------------------------------------------------------------
# Optional cron-based scheduled backups (borgmatic standalone mode)
# ---------------------------------------------------------------------------
SCHEDULE_ENABLED=$(bashio::config 'schedule_enabled')

if [ "$SCHEDULE_ENABLED" = "true" ]; then
    SCHEDULE_CRON=$(bashio::config 'schedule_cron')
    bashio::log.info "Scheduling borgmatic at: ${SCHEDULE_CRON}"
    echo "${SCHEDULE_CRON} root borgmatic --verbosity -1 >> /var/log/borgmatic.log 2>&1" \
        > /etc/cron.d/borgmatic
    chmod 0644 /etc/cron.d/borgmatic
    crond -b
fi

# ---------------------------------------------------------------------------
# Export API key as env var for api_server.py and start the API server
# ---------------------------------------------------------------------------
bashio::log.info "Starting BorgBackup API server on port 8774..."
export BORGBACKUP_API_KEY="$API_KEY"
export BORGBACKUP_REPO_PATH="$REPO_PATH"
export BORGBACKUP_PASSPHRASE="$PASSPHRASE"
export BORGBACKUP_KEEP_DAILY="$KEEP_DAILY"
export BORGBACKUP_KEEP_WEEKLY="$KEEP_WEEKLY"
export BORGBACKUP_KEEP_MONTHLY="$KEEP_MONTHLY"

exec python3 /api_server.py
