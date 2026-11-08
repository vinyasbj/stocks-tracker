# syntax=docker/dockerfile:1
FROM python:3.12-slim-bookworm

# No .pyc files; unbuffered logs so `docker logs` / Dozzle show lines as they happen
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src

# tzdata: zone data for log timestamps (TZ) and zoneinfo; procps: pgrep for the health check
RUN apt-get update && apt-get install -y --no-install-recommends \
    tzdata \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependencies first, so code-only changes reuse this layer
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY src/ src/

# config.ROOT resolves to /app, so the default DB_PATH (data/stockbot.db) lands in the volume
RUN mkdir -p /app/data && useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Liveness only (the process exists); the real signal is the HEARTBEAT_URL ping from each monitor pass
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD pgrep -f "python -m stockbot" > /dev/null || exit 1

CMD ["python", "-m", "stockbot"]
