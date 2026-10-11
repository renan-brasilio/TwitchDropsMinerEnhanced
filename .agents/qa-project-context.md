# QA Project Context — Twitch Drops Miner Enhanced

## Product

- **Name:** Twitch Drops Miner Enhanced
- **What it does:** Earns Twitch drop rewards automatically by simulating stream watching for eligible campaigns. It includes a web dashboard and a native macOS menu bar app.
- **Type:** Desktop utility / self-hosted daemon (local Mac or remote Docker server).
- **Environments / URLs:** Local daemon `http://127.0.0.1:23432`; remote Docker deployment on a user VPS (any URL, optional `API_KEY`). No staging environment.
- **Critical user journeys:**
  1. User authenticates with Twitch through device-code OAuth or a browser `auth-token`, and the miner validates the account.
  2. The miner fetches inventory and campaigns, then shows only actionable campaigns: active, upcoming, or ended with a still-claimable reward.
  3. The miner selects an eligible live channel by game priority and priority mode, watches it, and the home target shows the reward, campaign, and progress.
  4. The user clicks **Watch** on a tracked channel. The miner switches to it only when the channel can earn an active drop.
  5. A reward becomes claimable and is claimed automatically, when auto-claim is enabled, or manually with **Claim Drops**.
  6. The user edits settings (game priorities, priority mode, auto-claim) and they persist across restarts.
  7. The user disconnects the Twitch account. Mining stops, credentials and account data are removed, and the miner stays disconnected across restarts until an explicit reconnect.
  8. The macOS app connects to a local or remote miner, keeps running in the menu bar when its window closes, and reflects progress in the status item.

## Tech Stack

- **Backend:** Python 3.10+ (CI release builds on 3.12), asyncio, aiohttp (REST + WebSocket API server and Twitch HTTP/GQL client), yarl.
- **API style:** REST JSON under `/api/*`, a dashboard WebSocket at `/ws`, and Twitch PubSub WebSocket clients (`websocket.py`).
- **Frontend:** Single static page `web/index.html` (vanilla JS, no build step), served by `api.py`.
- **Native shell:** `mac/main.swift` (AppKit + WKWebView, single file compiled by `build_mac.sh` with `swiftc`). JS ↔ native bridge through `window.webkit.messageHandlers.settings` and the `native-settings` event.
- **State:** `StateHub` (`state_hub.py`) is the in-memory projection broadcast to clients. Persistence uses `settings.json` and `cookies.jar` in `DATA_DIR`. There is no database.
- **External dependencies:** Twitch GQL, Twitch ID OAuth (device flow, token validation), Twitch PubSub, and stream playlist endpoints.
- **Hosting:** Local macOS app bundle, or Docker Compose (`Dockerfile`, `docker-compose.yml`) on a VPS (Dokploy).
- **Legacy / tooling modules (not on the product path):** `gui.py` (upstream Tkinter GUI), `registry.py`, `cache.py`, `make_icon.py`, `make_twitch_miner_icon.py`, `render_screenshots.py`.

## Test Stack

### Unit / Integration (Python)
- **Framework:** stdlib `unittest` (`IsolatedAsyncioTestCase`) with `aiohttp.test_utils.TestClient`/`TestServer` for in-process API tests.
- **Test directory:** `tests/` (`test_<area>.py`).
- **Run:** `python -m unittest discover -s tests -v`.
- **Coverage:** coverage.py with branch coverage (`.coveragerc`), dev dependency in `requirements-dev.txt`.

### E2E (dashboard)
- **Framework:** Playwright for Python (Chromium), against a real in-process backend whose Twitch upstream is virtualized. It lives in `tests/e2e/`.

### Native macOS shell
- **Framework:** None. `main.swift` is a single executable target without an XCTest bundle; it is verified manually (see Risk Areas).

## CI/CD

- **Platform:** GitHub Actions.
- `.github/workflows/tests.yml` runs the full Python suite (unit, API, E2E) with coverage on every push and pull request. A coverage floor blocks merges on regression.
- `.github/workflows/release.yml` runs on `v*` tags. It runs the regression suite before building the app, DMG, and ZIP (`TwitchDropsMinerEnhanced-vX.Y.Z-macOS-arm64.*`) and publishing the GitHub release.
- `.github/workflows/ci.yml` is the inherited upstream multi-platform PyInstaller build and is not a quality gate.

## Environments

- **Development:** Local checkout with `venv/`; the backend runs with `python main.py --no-browser` on port 23432.
- **Production:** The user's Mac (`/Applications/TwitchDropsMinerEnhanced.app`) and/or a remote Docker daemon.
- **Parity:** No staging. Tests never touch real Twitch. Twitch is virtualized at the aiohttp session / upstream-server boundary, and the app's own API, lifecycle, and persistence run for real.

## Quality Goals

- **Suite:** Every regression test passes before any release tag publishes (enforced in `release.yml`).
- **Coverage:** Branch coverage, measured by coverage.py on product modules only (legacy/tooling modules omitted; reasons in `.coveragerc`). Gated in CI by `fail_under` in `.coveragerc`, currently **72%** total. That is the measured baseline after the regression suite landed, up from 30%; raise it whenever coverage improves and never lower it. Measured per module: `websocket.py` 90%, `inventory.py` 87%, `state_hub.py` 86%, `api.py` 78%, `utils.py` 68%, `twitch.py` 60%, `channel.py` 53%. Next targets: CRITICAL modules ≥ 80% (`api.py`, selection/lifecycle paths in `twitch.py`).
- **Flakiness:** 0 tolerated flaky tests. Tests are deterministic: fixed clock, no real network, no sleeps for synchronization.
- **Duration:** Unit + API suite < 60 s; E2E < 3 min.

## Risk Areas

Scored Impact (1–5) × Probability (1–5). Probability reflects churn over the last three months: `web/index.html` 17 commits, `mac/main.swift` 14, `api.py` 11, `twitch.py` 9, `state_hub.py` 8. The coverage column reflects the regression suite added in v1.4.2 (127 tests).

| # | Area | Impact | Prob. | Score | Zone | Business impact | Current coverage / gap |
|---|------|--------|-------|-------|------|-----------------|------------------------|
| 1 | Campaign eligibility, visibility & claiming (`inventory.py`, `StateHub.get_inventory`, claim endpoint) | 4 | 4 | 16 | CRITICAL | Rewards lost, or users see stale, unclaimable campaigns | `inventory.py` 87%. `test_campaign_visibility`, `test_drop_progress` (preconditions, earning window, ACL, progress, claim window, claim outcomes), claim endpoint in `test_api_contract`. Gap: live progress via PubSub drop events end-to-end |
| 2 | Channel selection & watching (`can_watch`, `should_switch`, `get_priority`, priority modes, Watch control) | 4 | 4 | 16 | CRITICAL | Mining the wrong game or nothing at all; progress silently stalls | `test_channel_selection` drives the real `_run` state machine through priority modes and switching. `twitch.py` 60%. Gap: the watch loop's stream-playlist requests |
| 3 | Account lifecycle (connect, disconnect, token replacement, cookies, persisted logout, rejected token) | 5 | 3 | 15 | CRITICAL | Credential leakage, or an account the user believes is disconnected keeps mining | `test_account_lifecycle` (10) + E2E account specs, including rejected-token recovery (bug fixed in v1.4.2) |
| 4 | REST API contract & remote security (`API_KEY` middleware, input validation, no credential echo) | 5 | 3 | 15 | CRITICAL | A remote daemon is controllable by anyone, or its token is exposed | `test_api_contract` (22): key sources, exemptions, CORS, atomic settings validation (bugs fixed in v1.4.2), credential non-exposure. `api.py` 78% |
| 5 | Dashboard UI state (Settings page, account controls visibility, auto-claim sync, priorities editor, Watch buttons, activation banner) | 3 | 5 | 15 | CRITICAL | Users act on wrong state (e.g. Connect shown while connected, hidden Disconnect) | `tests/e2e/test_settings_dashboard.py` (Playwright, 20 specs) |
| 6 | Native macOS shell (close-to-menu-bar, connection switch, settings bridge, status item) | 3 | 4 | 12 | HIGH | App quits unexpectedly or can't reach a remote miner | Web side of the bridge covered by E2E with a simulated `webkit` handler. Native code is manual only (no XCTest target). Release smoke: close window, open Settings, switch Local↔Remote |
| 7 | Twitch PubSub pool (`websocket.py`: topic sharding, reconnect, message dispatch) | 4 | 3 | 12 | HIGH | Progress and online status stop updating; drops not credited | `test_pubsub_pool` (11) against a fake PubSub server. `websocket.py` 90% |
| 8 | Settings persistence (`settings.py`, `settings.json` defaults & round-trip) | 3 | 2 | 6 | MEDIUM | Preferences reset after restart | `test_settings_persistence` (9). `settings.py` 94% |
| 9 | Release packaging consistency (`version.py` ↔ `build_mac.sh` plist ↔ artifact names) | 3 | 3 | 9 | MEDIUM | Mislabeled or missing release assets | `test_release_metadata` (4) |
| 10 | Translations (`lang/*.json`) | 2 | 2 | 4 | LOW | Wrong UI strings | JSON validated in `ci.yml` |

### Failure modes (score ≥ 10)

- **1 Campaign eligibility.**
  - **Trigger:** Twitch payload variants (preconditions, unlinked account, expired claim window, sub-only).
  - **Blast radius:** All users of affected campaigns.
  - **Detection:** User report.
  - **Mitigation:** Visibility tests.
  - **Gap:** Progress, preconditions, and `can_earn` / `can_claim` boundaries.
- **2 Channel selection.**
  - **Trigger:** Priority list edits, priority-mode changes, ACL-restricted campaigns, offline channels.
  - **Blast radius:** All mining.
  - **Detection:** Stalled progress.
  - **Mitigation:** Watch tests.
  - **Gap:** Mode ordering and switch rules.
- **3 Account lifecycle.**
  - **Trigger:** Disconnect during in-flight work, or a restart with an env token.
  - **Blast radius:** A single account.
  - **Detection:** Lifecycle tests.
  - **Mitigation:** Cancellation-and-join tests.
  - **Gap:** None known.
- **4 API security.**
  - **Trigger:** A daemon exposed publicly with `API_KEY`.
  - **Blast radius:** Remote account control.
  - **Detection:** None.
  - **Mitigation:** Middleware.
  - **Gap:** No tests of key sources (header, `X-API-Key`, query), exemptions, or 401 shape.
- **5 Dashboard UI.**
  - **Trigger:** Any `index.html` change.
  - **Blast radius:** All users.
  - **Detection:** User report.
  - **Mitigation:** Manual smoke.
  - **Gap:** No browser regression suite.
- **6 Native shell.**
  - **Trigger:** `main.swift` change.
  - **Blast radius:** macOS users.
  - **Detection:** Manual.
  - **Mitigation:** Manual release smoke (close, Settings, connection switch).
  - **Gap:** No automated native tests.
- **7 PubSub.**
  - **Trigger:** Twitch protocol change, or many topics.
  - **Blast radius:** All mining.
  - **Detection:** Stalled progress.
  - **Mitigation:** None.
  - **Gap:** Topic sharding and dispatch.

### Reassessment

Re-score affected rows within 48 hours of any user-reported defect, when a new feature area lands, or when Twitch API behavior changes. Re-score quarterly regardless, refreshing churn with:

```
git log --since="3 months ago" --name-only --format= | sort | uniq -c | sort -rn | head -20
```

## Team

- **Team:** Solo developer, the repository owner, assisted by coding agents. Dev:QA ratio is solo, so developers own all tests.
- **Methodology:** Continuous delivery with a release per batch of changes (see `AGENTS.md` locally).
- **QA engagement:** Regression tests accompany every fix/feature; release tags are gated on the suite.

## Conventions

- **Test files:** `tests/test_<area>.py`, `unittest` classes, `IsolatedAsyncioTestCase` for async code. E2E specs live in `tests/e2e/test_<flow>.py`.
- **Isolation:** Patch `settings.SETTINGS_PATH` / `twitch.COOKIES_PATH` (or `DATA_DIR`) to temp dirs, and use a fixed clock (`Clock` patch for `inventory.datetime`). Never use real Twitch, real user settings, or real credentials; synthetic tokens only.
- **Test data:** Builders that produce Twitch-shaped payloads (see `tests/test_campaign_visibility.py::campaign`).
- **Selectors (E2E):** Element `id`s already present in `index.html` (e.g. `#btnDisconnect`, `#settingsAutoClaimToggle`), and ARIA roles/names for headings and buttons.
- **Branching:** `master`; releases via annotated `vX.Y.Z` tags.
