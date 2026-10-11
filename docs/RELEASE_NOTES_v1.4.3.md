# v1.4.3

## Fixes

- **Expired Twitch activation codes are renewed.** If you didn't enter the device code before it expired, the miner asked for a new one with the wrong request, so Twitch rejected it and login stalled. Now a fresh code appears.
- **Choosing Local in Connection Settings… really goes local.** Picking **Local Daemon** in the native dialog while the URL field still held a remote address kept talking to the remote server, even though the window said Local.
- **API keys containing `+` work.** Keys with `+` (common in generated keys) were sent unescaped and arrived with a space instead, so a remote miner answered 401.
- **The menu bar countdown clears with the drop.** With the **Time left** style, the old countdown stayed in the menu bar after the active drop ended.
- **WebSocket error logging no longer crashes.** An error from Twitch PubSub raised a second error while being logged, instead of reconnecting cleanly.
- Removed the unused legacy username/password login code.

## Tests

- **Native macOS app:** the app's logic now lives in a small Swift package (`mac/Sources/MinerCore`) with 51 Swift tests. They cover connection rules, dashboard and WebSocket URLs, the settings bridge (including that the API key never reaches the page), status parsing, and menu bar text. CI runs them on macOS.
- **Native UI smoke (local):** five tests drive a separate test copy of the app against a fake miner: close to the menu bar and reopen, close to quit, Settings menu, and saving the connection. Run them with `RUN_NATIVE_UI=1`.
- **Mining runtime:** new tests cover the watch loop, the maintenance schedule, PubSub stream and drop events, request retries, GQL error handling, device-code login, channel stream status, and the playlist and segment watch requests.
- The Python suite grew from 127 to 256 tests. Branch coverage went from 72% to 87% (`twitch.py` 60→89%, `channel.py` 53→84%, `utils.py` 68→85%), and the coverage floor is now 86%.

## Downloads

- `TwitchDropsMinerEnhanced-v1.4.3-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.4.3-macOS-arm64.zip`: macOS application archive.
