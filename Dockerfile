# Twitch Drops Miner Enhanced: headless miner + web dashboard/API
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    HOST=0.0.0.0 \
    PORT=23432 \
    DOCKER=1 \
    HEADLESS=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
    && rm -rf /var/lib/apt/lists/*

# PyGObject only serves the desktop tray icon on Linux and needs a C toolchain
# plus cairo to build; the server never shows a tray, so it is left out.
COPY requirements.txt .
RUN grep -v '^PyGObject' requirements.txt > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

COPY . .
# The app rewrites lang/English.json on every start.
RUN useradd --uid 1000 --create-home miner && mkdir -p /app/data && chown -R miner /app/data /app/lang
USER miner

EXPOSE 23432
VOLUME ["/app/data"]

# "/" is served without the API key, so the check passes when API_KEY is set.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS -o /dev/null http://127.0.0.1:23432/ || exit 1

CMD ["python", "main.py", "--host", "0.0.0.0", "--no-browser"]
