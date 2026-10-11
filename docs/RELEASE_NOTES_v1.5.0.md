# v1.5.0

## New: Games page

A new **Games** tab replaces the old dropdown for choosing which games to mine.

- **Cover art grid.** Every game shows its Twitch box art. Select a cover to add it to your mining order, and select it again to remove it. Chosen games show their rank on the cover.
- **Every game on Twitch.** **With drops** shows the games with drop campaigns on your account right now. **All games** browses the full Twitch catalog, most watched first, loading more as you scroll.
- **Search.** Type any game name to search all of Twitch. If nothing matches, you can still add the name exactly as typed.
- **Mining order panel.** Your chosen games, with covers and drop campaign counts, ranked top to bottom. Reorder with `▲`/`▼`, remove with `✕`, and set the **Priority mode** in the same place. Changes save immediately.
- **Stale names are flagged.** A saved game that matches no Twitch game (for example a renamed title) is marked **Not found on Twitch**, because the miner can never mine it.
- **Settings → Game priorities** now shows a short summary with covers and a **Choose games** button that opens the Games tab.

## Fixes

- **Game names match Twitch case-insensitively.** Twitch spells some names differently from how they were typed, for example `VALORANT`. A saved `Valorant` was silently never mined, and excluding a game had the same problem. Priority and exclude lists now match regardless of case.
- **Campaign cards escape game names.** A game or campaign name with an apostrophe broke the card's **+ Prioritize** button.
- The old hard-coded "popular games" list, which contained mis-cased names, was removed in favor of Twitch's real catalog.

## Tests

- 7 new Playwright specs for the Games page: drop games view, adding and removing by cover, search (including names with apostrophes), adding an unmatched name, paging through the full catalog, reordering and mode, and the disconnected state. One spec found a real race where typing in the search box right after opening the tab was ignored; it is fixed.
- 5 new API contract tests for `/api/games/catalog` and `/api/games/lookup`: input limits, paging cursors, search text sent only as a GQL variable, Twitch failures returned as `502` without internal details, and `409` without an account.
- 1 new selection test proving priority and exclude lists match Twitch names case-insensitively.
- Suite: 278 Python tests and 59 Swift tests. Branch coverage 89.0%.

## Downloads

- `TwitchDropsMinerEnhanced-v1.5.0-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.5.0-macOS-arm64.zip`: macOS application archive.
