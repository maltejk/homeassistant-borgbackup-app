ARG BUILD_FROM
FROM ${BUILD_FROM}

# Install Borg (repository engine), borgmatic (scheduled backups), the SSH
# client (remote repositories), curl (Supervisor discovery), and the Python
# API-server dependencies.
# hadolint ignore=DL3018
RUN apk add --no-cache \
        borgbackup \
        borgmatic \
        openssh-client \
        curl \
        python3 \
        py3-aiohttp

# Copy add-on scripts
COPY run.sh /run.sh
COPY api_server.py /api_server.py

RUN chmod a+x /run.sh

CMD ["/run.sh"]
