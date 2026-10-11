# v1.4.0

## Settings

- **Miner connection** in **Settings → Application**: choose **Local (this Mac)** or **Remote server (Docker)**, enter the server URL and optional API key, then **Save & reconnect**. The dashboard reloads from the chosen miner.
- The API key stays in the macOS app. The page only knows whether a key is saved; leave the field blank to keep it, or choose **Remove saved API key**.
- Invalid server URLs are rejected with a visible message. If the selected miner can't be reached, the offline screen now has a **Connection Settings…** button, and the native menus keep the same option.
- The Settings tab no longer hides the dashboard overview; the active target and stats stay visible above it.

## Verification

- All 17 regression tests passed; the release workflow runs them before packaging.
- In the native app, switching to **Remote server** and back to **Local** through Settings reconnected each time (window title showed Remote, then Local). The account, selected streamer, and preferences were unchanged.

## Downloads

- `TwitchDropsMinerEnhanced-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-macOS-arm64.zip`: macOS application archive.
