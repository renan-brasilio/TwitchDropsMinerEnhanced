# v1.7.0

## New: mining options in Campaigns & Rewards

- **Mine only selected games**: mines only the games in your mining order. Turned off, those games go first and campaigns of other games follow. This is the Games page's **Priority mode** ("Priority Games Only"), so the two stay in sync, and turning it off restores the order you last picked there.
- **Automatically select linked games**: adds the game of every campaign Twitch reports as linked to your account to the end of your mining order, so it gets mined without picking it yourself. Each campaign is handled once: remove a game and it stays removed until it gets a new campaign. Excluded games, ignored campaigns and campaigns whose link status is unknown are skipped. Off by default.

## Fix: Campaigns & Rewards jumped back to the top

Every 2.5 seconds the dashboard redrew the campaign list and the channel table from a status update that doesn't contain them, so both were briefly empty. The page shrank, and the scroll position snapped back to the top. Lists are now redrawn only from data that contains them.

## Tests

New tests cover the automatic selection in the real mining logic, the new setting in the API, and both options plus the scroll position in the browser. The scroll test fails on the previous version.

## Downloads

- `TwitchDropsMinerEnhanced-v1.7.0-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.7.0-macOS-arm64.zip`: macOS application archive.
