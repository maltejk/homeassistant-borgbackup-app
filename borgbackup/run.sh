#!/usr/bin/with-contenv bashio
# shellcheck shell=bash
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
    # Printed only once, at generation time — with Supervisor discovery
    # handling setup automatically, logging the live key on every restart
    # is needless plaintext-credential exposure in the log.
    bashio::log.info "Generated API key: ${API_KEY}"
    bashio::log.info "Enter this key in the BorgBackup integration to connect."
fi

API_KEY=$(cat "$API_KEY_FILE")
bashio::log.info "BorgBackup API key ready (…${API_KEY: -4}). Supervisor discovery configures the integration automatically; for manual setup, read /data/api_key."

# ---------------------------------------------------------------------------
# Publish Supervisor discovery so the integration is offered for auto-setup.
#
# Home Assistant routes a discovery message to the config flow whose domain
# matches the "service" field, calling its async_step_hassio with "config".
# Requires hassio_api: true and a matching "discovery:" entry in config.yaml.
# ---------------------------------------------------------------------------
if bashio::supervisor.ping 2>/dev/null; then
    # The Supervisor-assigned hostname is <repo-hash>-<slug> when the add-on
    # was installed from a repository, and only "local-<slug>" for add-ons
    # installed straight from a local directory. Ask the Supervisor rather
    # than hardcoding the local-only form, or discovery hands the
    # integration a host it can never resolve.
    ADDON_HOSTNAME=$(bashio::addon.hostname)
    if [ -z "$ADDON_HOSTNAME" ]; then
        ADDON_HOSTNAME="local-borgbackup"
    fi
    # Build the JSON config with python so the API key is safely encoded.
    export BORG_DISCOVERY_KEY="$API_KEY"
    export BORG_DISCOVERY_HOST="$ADDON_HOSTNAME"
    DISCOVERY_CONFIG=$(python3 -c "import json, os; print(json.dumps({'host': os.environ['BORG_DISCOVERY_HOST'], 'port': 8774, 'api_key': os.environ['BORG_DISCOVERY_KEY']}))")
    unset BORG_DISCOVERY_KEY BORG_DISCOVERY_HOST
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
# Read add-on options
# ---------------------------------------------------------------------------
bashio::config.require 'repo_path'
REPO_PATH=$(bashio::config 'repo_path')
PASSPHRASE=$(bashio::config 'passphrase')
SSH_PRIVATE_KEY=$(bashio::config 'ssh_private_key')
INIT_IF_NOT_EXISTS=$(bashio::config 'init_if_not_exists')
KEEP_DAILY=$(bashio::config 'keep_daily')
KEEP_WEEKLY=$(bashio::config 'keep_weekly')
KEEP_MONTHLY=$(bashio::config 'keep_monthly')

mkdir -p /root/.ssh
chmod 700 /root/.ssh

# Persist SSH host keys across restarts so a remote repo doesn't hit an
# interactive "Host key verification failed." prompt on every start.
touch /data/known_hosts
chmod 600 /data/known_hosts

# Write SSH private key if provided. Named neutrally (not id_rsa/id_ed25519)
# since the key type is whatever the user supplied; ssh identifies it by
# file content, not name. A key pasted without a trailing newline is
# rejected by some ssh versions, so make sure one is always present.
if [ -n "$SSH_PRIVATE_KEY" ]; then
    printf '%s\n' "$SSH_PRIVATE_KEY" > /root/.ssh/id_borg
    chmod 600 /root/.ssh/id_borg
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
