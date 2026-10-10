# Twitch Drops Miner Enhanced v1.1.0

## New features

### Campaign schedules
- Campaign cards show start and end dates in your local timezone.
- Each card shows time remaining or **Ended**.
- Inventory API responses and WebSocket inventory snapshots include timezone-aware `starts_at` and `ends_at` timestamps.

### Configurable automatic reward claiming
- Added **Auto-claim drops** to the **Campaigns & Rewards** header.
- Enabled by default to preserve existing automatic claiming behavior.
- The preference is saved across backend restarts and applies to inventory-based claiming and incoming reward-ready events.
- Disable it to leave rewards available for manual **Claim**. Dependent rewards may require the previous reward to be claimed before progressing.
- The dashboard reports failed setting updates instead of showing success and restores the previous checkbox state on failure.

### Close to Menu Bar
- Added **Close to Menu Bar** to the native app menu and menu bar dropdown.
- Hides the dashboard and removes the Dock icon while keeping the app and menu bar status active.
- **Open Dashboard Window** restores the dashboard and Dock icon.
- Added a saved **Keep Running in Menu Bar When Window Closes** preference in both menus, enabled by default.
- The red close button and `Cmd+W` follow this preference. Disable it to quit when closing the window.
- **Quit** / `Cmd+Q` still exits the app.

## Installation

Download `TwitchDropsMinerEnhanced-macOS-arm64.dmg`, open it, and drag `TwitchDropsMinerEnhanced.app` into **Applications**. A portable ZIP is also available. Quit an older running app before replacing it.

## Verification

- Live dashboard displayed campaign schedules from Twitch inventory.
- Auto-claim setting was toggled off and on through the dashboard; API state and dashboard reload persistence were verified. No newly claimable Twitch reward arrived during that check, so a new reward claim was not exercised.
- Native macOS UI automation verified closing to the menu bar, reopening the dashboard, saved close preference, and quitting on close when that preference is disabled.
