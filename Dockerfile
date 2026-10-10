# Twitch Drops Miner Enhanced - Docker Container
FROM python:3.12-slim

# Prevent python from writing pyc files and buffering stdout
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    HOST=0.0.0.0 \
    PORT=23432 \
    DOCKER=1 \
    HEADLESS=1

WORKDIR /app

# Install system dependencies & CA certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt truststore

# Copy application code
COPY . .

# Expose Web Dashboard & API port
EXPOSE 23432

# Data volume for cookies.jar, settings.json, cache, logs
VOLUME ["/app/data"]

# Healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://127.0.0.1:23432/api/status || exit 1

# Start headless miner daemon with API server
CMD ["python", "main.py", "--host", "0.0.0.0", "--no-browser"]
