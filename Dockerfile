FROM python:3.12-slim

WORKDIR /app

# System deps kept minimal; pandas wheels are prebuilt for slim.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY scripts/ ./scripts/

# Data (SQLite + dropped Letterboxd ZIPs) lives on a mounted volume.
RUN mkdir -p /app/data/imports
ENV DATABASE_PATH=/app/data/watch.db \
    IMPORT_DIR=/app/data/imports

EXPOSE 8000

# Runs as root on purpose: /app/data is a bind mount owned by a host user,
# and a rebuilt image would silently lose DB write access if we
# guessed a UID here. Revisit with a PUID build-arg if it ever matters.

# Probes /healthz, not /. `/` renders the index template from settings alone
# and never opens the database, so it reported healthy through the one failure
# worth catching: a container that has lost its /app/data bind mount, where
# every page that reads a film 500s. /healthz runs a count over `films` and
# answers 503 if that fails; urlopen raises on 503, so the check goes red.
# Compose inherits this — it is deliberately not restated in
# docker-compose.yml, so there is one place to change the interval.
HEALTHCHECK --interval=60s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
