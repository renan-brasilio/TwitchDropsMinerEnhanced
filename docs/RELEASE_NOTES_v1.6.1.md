# v1.6.1

## Fix: Docker image

- **The image now builds.** `PyGObject`, which only the Linux desktop tray uses, needed a C toolchain and cairo that the slim base image lacks. The server image leaves it out.
- **The health check works with `API_KEY` set.** It called `/api/status`, which answers `401` without the key, so the container stayed unhealthy and Docker Swarm (used by Dokploy) kept restarting it. It now checks the dashboard page `/`, which needs no key.
- **Local secrets are no longer copied into the image.** There was no `.dockerignore`, so building from a working copy baked in `cookies.jar`, `settings.json`, the virtualenv and release archives.
- **The container runs as a non-root user** (`miner`, uid 1000).
- **`docker-compose.yml` no longer publishes the port to the internet.** It binds `127.0.0.1:23432`, keeps data in a named volume, and reads `API_KEY` from the environment. Put a reverse proxy with HTTPS in front to expose it.

## Tests

A new CI job builds the image and checks that it turns healthy with `API_KEY` set, that the API rejects requests without the key, and that settings survive a container restart.

## Downloads

- `TwitchDropsMinerEnhanced-v1.6.1-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.6.1-macOS-arm64.zip`: macOS application archive.
