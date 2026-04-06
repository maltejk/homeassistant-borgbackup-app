FROM borgmatic/borgmatic:latest

# Install aiohttp for the API server
RUN pip install --no-cache-dir aiohttp

# Copy add-on scripts
COPY run.sh /run.sh
COPY api_server.py /api_server.py

RUN chmod +x /run.sh

CMD ["/run.sh"]
