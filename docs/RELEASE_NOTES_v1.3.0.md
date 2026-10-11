# v1.3.0

## Settings

- A single **Settings** page replaces the separate Game Priorities and Twitch Authentication tabs. It groups Twitch account controls, automatic reward claiming, the existing game-priority editor, and native application preferences.
- Open Settings from the dashboard or with **Cmd+,** in the macOS app. Local/remote miner connection configuration remains under **Connection Settings...** in the native app and status menus.
- The macOS Application section exposes the saved close-to-menu-bar preference and menu bar display style. These controls are hidden in ordinary browsers and wait for native acknowledgement before reporting a change.
- Auto-claim preferences stay synchronized between Settings and Campaigns & Rewards. Priority selection, custom games, ordering, and priority modes remain available.

## Twitch account

- **Disconnect** asks for confirmation, stops account activity, removes the miner's saved Twitch token/cookies, and clears account-specific channels, campaigns, progress, identity, activation code, and topics without closing the dashboard.
- Disconnect persists across backend restarts, including when an environment token is configured. Mining controls cannot silently reconnect the account; preferences remain saved.
- **Connect with Twitch** explicitly starts OAuth device activation. Advanced browser-token authentication also reconnects and safely replaces the previous account; the password field clears after saving.
- Disconnect is local to the connected miner. It does not sign out Twitch in the browser or revoke Twitch authorization. When connected to a remote miner, it disconnects that server's Twitch account.
- Account requests, callbacks, and background tasks are cancelled and joined before account data is cleared, preventing old account activity from restoring stale state.

## Verification

- All **17 regression tests** passed: seven existing campaign/channel tests and ten account lifecycle tests. The release workflow runs the suite before packaging.
- An isolated running dashboard exercised preference persistence, disconnect confirmation/cancel, credential removal, rejected mining controls, persisted logout after restart with an environment token, device activation, and token-based account replacement. Only Twitch upstream transport was virtualized; API, lifecycle, and settings persistence were real.
- The native Settings page and close-preference bridge were exercised. The real account remained authenticated, and its saved priorities, auto-claim preference, close preference, menu bar style, and selected eligible streamer were preserved.

## Downloads

- `TwitchDropsMinerEnhanced-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-macOS-arm64.zip`: macOS application archive.

Replace the existing app in Applications, then launch the updated copy.
