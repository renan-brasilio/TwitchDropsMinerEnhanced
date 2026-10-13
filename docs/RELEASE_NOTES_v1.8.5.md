# v1.8.5

## Fix: Active Target empty state layout

- **Fixed narrow 72px squeezed column bug**: When no campaign was active, hiding the thumbnail previously caused CSS Grid to drop the text into the 72px thumbnail column, wrapping every word vertically. The card body now switches to full-width block layout when empty.
- **Added direct navigation links**: The empty-state guidance text now includes clickable links directly to Campaigns & Rewards.

## Downloads

- `TwitchDropsMinerEnhanced-v1.8.5-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.8.5-macOS-arm64.zip`: macOS application archive (the in-app updater installs from this file).
