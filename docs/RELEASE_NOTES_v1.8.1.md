# v1.8.1

## New: published Docker image

- Every release now publishes the miner image to GitHub Container Registry: `ghcr.io/renan-brasilio/twitchdropsminerenhanced`, tagged `X.Y.Z`, `X.Y` and `latest`, for `linux/amd64` and `linux/arm64`.
- `docker-compose.yml` uses that image (`latest` by default, `TDME_TAG` to pin a version), so a server no longer builds the miner itself.
- To stay on the newest release, run `docker compose pull && docker compose up -d` on a schedule. It recreates the container only when a newer image exists; settings and the Twitch login stay in the volume.

## Downloads

- `TwitchDropsMinerEnhanced-v1.8.1-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.8.1-macOS-arm64.zip`: macOS application archive (the in-app updater installs from this file).
