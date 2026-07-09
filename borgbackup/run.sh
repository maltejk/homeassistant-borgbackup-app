#!/usr/bin/with-contenv bashio
set -e

API_KEY_FILE=/data/api_key

# ---------------------------------------------------------------------------
# Generate API key on first run
# ---------------------------------------------------------------------------
if [ ! -f "$API_KEY_FILE" ]; then
    bashio::log.info "Generating new API key..."
    # Use python's secrets module: a single process with no pipeline, so it
    # cannot abort run.sh via SIGPIPE (exit 141) under `set -e`/pipefail.
    API_KEY=$(python3 -c "import secrets; print(secrets.token_urlsafe(30))")
    echo "$API_KEY" > "$API_KEY_FILE"
    chmod 600 "$API_KEY_FILE"
fi

API_KEY=$(cat "$API_KEY_FILE")
bashio::log.info "BorgBackup API key: ${API_KEY}"
bashio::log.info "Enter this key in the BorgBackup integration to connect."

# ---------------------------------------------------------------------------
# Publish Supervisor discovery so the integration is offered for auto-setup.
#
# Home Assistant routes a discovery message to the config flow whose domain
# matches the "service" field, calling its async_step_hassio with "config".
# Requires hassio_api: true and a matching "discovery:" entry in config.yaml.
# ---------------------------------------------------------------------------
if bashio::supervisor.ping 2>/dev/null; then
    # Build the JSON config with python so the API key is safely encoded.
    export BORG_DISCOVERY_KEY="$API_KEY"
    DISCOVERY_CONFIG=$(python3 -c "import json, os; print(json.dumps({'host': 'local-borgbackup', 'port': 8774, 'api_key': os.environ['BORG_DISCOVERY_KEY']}))")
    unset BORG_DISCOVERY_KEY
    if curl -sf -X POST \
        -H "Authorization: Bearer ${SUPERVISOR_TOKEN}" \
        -H "Content-Type: application/json" \
        -d "{\"service\": \"borgbackup\", \"config\": ${DISCOVERY_CONFIG}}" \
        "http://supervisor/discovery" > /dev/null; then
        bashio::log.info "Published discovery to Supervisor; integration can auto-configure."
    else
        bashio::log.warning "Could not publish discovery message (setup the integration manually)."
    fi
else
    bashio::log.info "Supervisor not reachable; skipping discovery."
fi

# ---------------------------------------------------------------------------
# Write borgmatic config from add-on options
# ---------------------------------------------------------------------------
REPO_PATH=$(bashio::config 'repo_path')
PASSPHRASE=$(bashio::config 'passphrase')
SSH_PRIVATE_KEY=$(bashio::config 'ssh_private_key')
INIT_IF_NOT_EXISTS=$(bashio::config 'init_if_not_exists')
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
export BORGBACKUP_INIT_IF_NOT_EXISTS="$INIT_IF_NOT_EXISTS"
export BORGBACKUP_KEEP_DAILY="$KEEP_DAILY"
export BORGBACKUP_KEEP_WEEKLY="$KEEP_WEEKLY"
export BORGBACKUP_KEEP_MONTHLY="$KEEP_MONTHLY"

exec python3 /api_server.py
