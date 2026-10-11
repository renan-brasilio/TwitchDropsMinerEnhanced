# v1.4.2

## Fixes

- **A token Twitch rejects no longer stops the miner.** Before this release, a rejected token (from **Settings → Advanced** or a saved session) ended the miner's main loop. No error appeared, and even a valid token entered afterwards never connected until the app restarted. Now the rejected token is removed, Settings shows "Twitch rejected the saved login. Connect again.", and connecting again with OAuth or a valid token works right away.
- **Settings updates are validated as a whole.** `POST /api/settings` now returns `400` for anything other than a JSON object, for non-string game lists, for unknown priority modes, and for non-boolean flags. Before, the string `"false"` turned auto-claim **on**, `null` returned a server error, and one bad field could half-apply an update. Valid dashboard updates are unchanged.

## Regression suite

The suite grew from 17 to 127 tests, covering the scored risk areas in `.agents/qa-project-context.md`:

| Area | Tests |
|------|-------|
| Drop eligibility, progress, and claim window | `test_drop_progress` |
| Channel selection and priority modes (real mining state machine) | `test_channel_selection` |
| REST API contract, `API_KEY` protection, credential non-exposure | `test_api_contract` |
| Twitch PubSub topic sharding, dispatch, and reconnects | `test_pubsub_pool` |
| Settings persistence and recovery | `test_settings_persistence` |
| Version and release asset naming consistency | `test_release_metadata` |
| Dashboard Settings, account controls, auto-claim, priorities, native bridge (Playwright) | `tests/e2e/test_settings_dashboard.py` |

- Branch coverage of product modules rose from 30% to 72%, and a coverage floor now blocks regressions.
- A new **Tests** workflow runs the full suite on Python 3.10 and 3.12 for every push and pull request. Releases run it, including E2E, before packaging.

## Downloads

- `TwitchDropsMinerEnhanced-v1.4.2-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.4.2-macOS-arm64.zip`: macOS application archive.
