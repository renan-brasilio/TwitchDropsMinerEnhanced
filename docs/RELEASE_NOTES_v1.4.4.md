# v1.4.4

## Quality & Coverage

- **`api.py` coverage reached 95.5%** (was 78.4%): added full test coverage for control endpoints (`/api/control/pause`, `/api/control/resume`, `/api/control/reload`, `/api/control/switch`, and `/api/control/watch`), websocket control action handling, inventory refresh error handling, auth error handling, and server port fallback.
- **Native macOS menu builder unit tests:** extracted menu construction logic into `StatusMenuBuilder` with 8 new Swift tests (bringing `MinerCore` to 59 tests in 13 suites, 96.2% line coverage). Tests run in CI covering all items, shortcuts, dynamic drop/streamer info, device activation items, and display submenus.
- **Every product module is now above 84% branch coverage:**
  - `websocket.py`: 90.3%
  - `api.py`: 95.5%
  - `settings.py`: 94.0%
  - `twitch.py`: 89.3%
  - `state_hub.py`: 88.9%
  - `inventory.py`: 87.4%
  - `utils.py`: 85.4%
  - `channel.py`: 84.2%
- Total product branch coverage rose to **88.6%**, and the CI coverage floor is now **88%**.
- Suite total is now **266 Python tests** and **59 Swift tests**, all passing in CI.

## Downloads

- `TwitchDropsMinerEnhanced-v1.4.4-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.4.4-macOS-arm64.zip`: macOS application archive.
