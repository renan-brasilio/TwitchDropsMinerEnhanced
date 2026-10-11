# Twitch Drops Miner Enhanced ⛏️

> **Fork of [DevilXD/TwitchDropsMiner](https://github.com/DevilXD/TwitchDropsMiner)**  
> While we appreciate the core concept of the original project, we needed a modern solution that **actually works reliably**. The original application suffered from severe Tkinter main-thread UI lag on macOS, a dated layout, broken Twitch device authentication (`KeyError: 'device_code'`), and outdated GraphQL query schemas.  
>  
> This project is a ground-up refactor featuring an event-driven headless engine, a native macOS desktop window, a menu bar status extra, a dark-mode stream console, and direct browser cookie login.
>  
> ⚠️ **Platform Availability:**  
> • **macOS**: ✅ **Fully Functional & Optimized** (Native `.app` window, `.dmg` installer, and Menu Bar extra).  
> • **Docker / VPS**: ✅ **Functional Daemon** (`Dockerfile` and `docker-compose.yml` for 24/7 remote hosting; the image is built and smoke-tested in CI).
> • **Windows & Linux**: 🚧 **Work in Progress (WIP)** (Headless daemon and web dashboard work; native desktop system tray integration in progress).

---

### 📸 Screenshots

#### Native macOS Application Window
![Twitch Drops Miner Enhanced - App Window](docs/images/app_window.png)

#### macOS Menu Bar Status Extra
![macOS Menu Bar Status Extra](docs/images/menubar_status.png)

---

### ✨ Key Features

- **🍎 Full Native macOS Desktop Window + Menu Bar Status Extra**:
  - **Desktop Window (`WKWebView`)**: Responsive dark-mode console displaying your live campaign progress, game box art covers, item reward icons, and 20 monitored streams.
  - **Channel Selection**: **Watch** in the tracked-channel list switches the miner to that eligible live channel; it does not open Twitch in a browser. Offline channels are disabled, and unavailable or ineligible channels report an error.
  - **Active Target**: Shows the campaign name as well as the reward. Inventory refresh rebinds the target to fresh campaign data and clears it when the current channel can no longer earn a reward.
  - **Menu Bar Extra (`NSStatusItem`)**: Custom Twitch + pickaxe template icon in your macOS menu bar displaying real-time progress (`[████████░░] 85%`), active streamer, viewer counts, and quick actions upon clicking.
  - **Close to Menu Bar**: Hides the window and removes the Dock icon while mining and menu bar status remain active. Use **Open Dashboard Window** in the status menu to restore the window and Dock icon.
  - **Window Close Preference**: **Keep running in the menu bar when closing the window** is enabled by default, available in **Settings → Application** and both native menus, and saved across restarts. It applies to the red close button and `Cmd+W`; disable it to quit when closing the window. **Quit** (`Cmd+Q`) always exits the app.
  - **Settings**: One tab for the Twitch account, auto-claim preference, a summary of your game priorities, and native application preferences, shown below the dashboard overview. Open the **Settings** tab or press `Cmd+,` on macOS. **Application** controls include the close preference, menu bar display style, and miner connection (local or remote Docker server); they appear only in the native app.
  - **About** (bottom of Settings): the miner's version (and where it runs), links to this GitHub repository, its releases and issues. In the Mac app it also shows the app version and **Check for updates**.
- **⚡ Zero-Lag Decoupled Architecture**:
  - Replaced the blocking 50–100Hz Tkinter polling loop with an async, event-driven `StateHub` that consumes 0.0% idle CPU.
- **🔐 Modern Authentication & Captcha-Free Login**:
  - **Fixed `KeyError: 'device_code'`**: Switched to Twitch's active `SMARTBOX` client for OAuth device-code activation.
  - **Quick Copy**: Click-to-copy button for the activation code in the app banner and menu bar.
  - **Direct `auth-token` Cookie Login**: Paste your browser's Twitch `auth-token` cookie under **Settings → Twitch account → Advanced**. The password field clears after saving, and credentials are never returned in status/settings responses.
  - **Disconnect & Reconnect**: While an account is connected, **Settings → Twitch account** shows only **Disconnect**. Confirm it to stop mining and remove this app's saved token, cookies, and account-specific data. The app stays disconnected across restarts, even with `TWITCH_AUTH_TOKEN` configured, until you explicitly connect again. Preferences remain saved. Once disconnected, **Connect with Twitch** starts device activation, and saving a browser token under **Advanced** also reconnects. This is local to the miner—it does not sign out your browser or revoke Twitch authorization. On a remote connection, it disconnects the account on that miner server.
- **🎮 Games Page (choose what to mine)**:
  - The **Games** tab shows game cover art in a grid. Select a cover to add it to your mining order; select it again to remove it. Chosen games show their rank (`#1`, `#2`, …) on the cover.
  - **With drops** lists the games that have drop campaigns on your account right now. **All games** browses every category on Twitch, most watched first, loading more as you scroll.
  - **Search** finds any game on Twitch by name. If Twitch has no game with that name, you can still add it exactly as typed.
  - The **Mining order** panel beside the grid lists your games top to bottom with their covers and drop campaign counts. Reorder with `▲`/`▼`, remove with `✕`, and pick the **Priority mode** there. Changes save immediately.
  - A saved game that no longer matches any Twitch game (for example after a rename) is marked **Not found on Twitch**, since it can never be mined.
  - Game names match Twitch case-insensitively, so a saved `Valorant` mines Twitch's `VALORANT`.
  - Browsing and search need a connected Twitch account. The `+ Prioritize` button on every campaign card still adds that game in one click.
- **Campaign discovery (Twitch-only)**:
  - Since October 2026 Twitch no longer gives the drop-campaign list to the device-code login this app uses. The miner finds campaigns on Twitch itself instead. It checks live **Drops Enabled** streams for every game in your mining order, plus the most-watched Drops Enabled streams across Twitch, and asks those channels which campaigns they offer.
  - Campaigns found this way show **Link unknown**, because Twitch no longer says whether your account is linked to the publisher. They are mined only while their game is in your mining order on the Games page. Choosing a game means you've linked its account. Other discovered campaigns are listed but not mined.
  - Campaigns already in progress keep Twitch's full, account-specific data, including link status and progress.
  - Only campaigns with someone streaming them right now are found, and those are the only ones that can be mined. Adding a game to your mining order refreshes campaigns right away.
- **Campaign schedule and reward claiming**:
  - Each campaign shows its start and end dates in your local timezone, plus time remaining.
  - **Mine only selected games** (top of the tab) mines only the games in your mining order. Turned off, those games go first and campaigns of other games follow. It is the same setting as **Priority mode** on the Games page ("Priority Games Only"), so both stay in sync.
  - **Automatically select linked games** adds the game of every campaign Twitch reports as linked to your account to the end of your mining order. Each campaign is handled once: a game you remove stays removed until it gets a new campaign, and excluded games and ignored campaigns are skipped. Off by default.
  - **Mine now** on a campaign switches to a channel for it and mines it before anything else, even if its game isn't in your mining order or its link status is unknown. The card shows **Mining first**; **Stop** returns to your normal order. The choice clears itself when the campaign is finished or over, and survives restarts.
  - **Ignore** hides a campaign and the miner never works on it; rewards you already earned are still claimed. Ignored campaigns move to **Ignored campaigns** at the bottom of the tab, where **Restore** brings them back. They don't count in totals or in **Games → With drops**.
  - **Games → With drops** has a **Refresh** button that checks Twitch again for live drop campaigns.
  - Fully claimed campaigns are hidden. Ended campaigns remain visible only while this account has an eligible, unclaimed reward within Twitch's claim window; only those claimable rewards are listed for ended campaigns.
  - Campaign totals and game-picker counts use the same filtered inventory and update after claims.
  - Toggle **Automatically claim ready drops** in **Settings → Rewards**, or use the synchronized **Auto-claim drops** shortcut in **Campaigns & Rewards**. Enabled by default to preserve existing behavior; saved across backend restarts.
  - When disabled, rewards remain available for manual claiming using **Claim**. Dependent rewards may require the previous reward to be claimed before progressing.
- **🐳 Docker & Remote VPS Support**:
  - Headless daemon container ready for deployment on any VPS (via Docker Compose or Dokploy).
  - **Client-Server Switcher**: In **Settings → Application → Miner connection**, choose **Local (this Mac)** (`http://127.0.0.1:23432`) or **Remote server (Docker)** with its URL and optional API key, then **Save & reconnect**. The API key stays in the macOS app and is never shown in the page. If the miner can't be reached, use **Connection Settings…** on the offline screen or in the native menus.
  - Optional `API_KEY` Bearer authentication for secure public hosting.

---

### 🚀 Installation (macOS Apple Silicon)

1. **Download the latest release:**
   - **`TwitchDropsMinerEnhanced-vX.Y.Z-macOS-arm64.dmg`**: Drag-and-drop installer disk image (for example `TwitchDropsMinerEnhanced-v1.4.1-macOS-arm64.dmg`).
   - **`TwitchDropsMinerEnhanced-vX.Y.Z-macOS-arm64.zip`**: Portable application archive.
2. **Install:**
   - Double-click the `.dmg` and drag **Twitch Drops Miner Enhanced** into your **Applications** folder.
3. **Launch:**
   - Open **Twitch Drops Miner Enhanced** from Applications or Spotlight.
   - The app window and menu bar icon will appear.
   - Authenticate via the Twitch device-code prompt, or open **Settings → Twitch account** to connect or paste a browser `auth-token` under **Advanced**.
4. **Updates:**
   - The app checks GitHub Releases at launch and once a day (turn this off in **Settings → About**), or on demand with **Check for Updates…** in the app menu, the menu bar menu, or Settings → About.
   - **Install** downloads the release's `.zip`, verifies it against the SHA-256 checksum GitHub publishes for it, checks it is this app at the announced version, then quits, swaps the app in place and relaunches it. If copying fails, the previous app is put back. The app must be in a folder you can write to (such as Applications).
   - The app update doesn't update the miner. A Docker miner updates when you redeploy it with the new tag; About notes when the miner is behind the latest release.

---

### 🐳 Remote VPS Deployment (Docker / Dokploy)

The container runs the same miner and dashboard without a window. Everything the dashboard controls (Twitch login, mining order, priority mode, auto-claim, **Mine now**, **Ignore**) is stored in the container's `/app/data` volume, so the Mac app, a browser and your phone all see one copy. The **Application** settings (close to menu bar, menu bar style, miner connection) belong to the Mac app and stay on the Mac. Run only one miner per Twitch account.

1. **Run it:**
   ```bash
   API_KEY=$(openssl rand -hex 24) docker compose up -d
   ```
   This pulls the published image `ghcr.io/renan-brasilio/twitchdropsminerenhanced` (`linux/amd64` and `linux/arm64`). Every release publishes it as `X.Y.Z`, `X.Y` and `latest`; the compose file uses `latest`, or set `TDME_TAG=1.8.0` to pin a version. The compose file publishes the dashboard on `127.0.0.1:23432` only. To expose it, put it behind a reverse proxy with HTTPS (on Dokploy/Traefik: remove `ports`, attach the service to the proxy network and route to container port `23432`).

   **Staying up to date:** `docker compose pull && docker compose up -d` recreates the container only when a newer image exists; settings and login stay in the volume. Run it on a schedule (cron or a systemd timer) to follow `latest` automatically.

2. **Environment variables:**
   ```env
   API_KEY=...             # Required once the dashboard is reachable from other machines
   TWITCH_AUTH_TOKEN=...   # Optional: seeds the Twitch login on first start
   TZ=Europe/Luxembourg    # Optional: timezone for logs
   ```
   With `API_KEY` set, every `/api` call needs `Authorization: Bearer <key>`, `X-API-Key: <key>` or `?key=<key>`. The dashboard page itself and the container health check (`GET /`) need no key.

3. **Sign in to Twitch:** open the dashboard (`https://your-domain/?key=<key>`), choose **Connect**, and enter the code shown at twitch.tv/activate from any device. To move an existing Mac setup instead, copy `settings.json` and `cookies.jar` into the volume before the first start.

4. **Connect from the macOS app:** **Settings → Application → Miner connection → Remote server (Docker)**, enter the URL and API key, then **Save & reconnect**. In Remote mode the app does not start its own local miner.

---

### 🛠️ Building from Source (macOS)

```bash
git clone https://github.com/renan-brasilio/TwitchDropsMinerEnhanced.git
cd TwitchDropsMinerEnhanced

# Set up virtual environment
python3 -m venv venv
./venv/bin/pip install -r requirements.txt truststore

# Build the native macOS application, DMG installer, and ZIP archive
./build_mac.sh

# Launch
open TwitchDropsMinerEnhanced.app
```

---

### Running the Regression Suite

```bash
./venv/bin/pip install -r requirements-dev.txt
./venv/bin/python -m playwright install chromium

# Unit, API contract, PubSub, settings, release-metadata and dashboard E2E tests, with branch coverage
REQUIRE_E2E=1 ./venv/bin/coverage run -m unittest discover -s tests -v
./venv/bin/coverage report
```

- Tests never contact Twitch. Twitch is replaced by local fake servers at the network boundary, and the miner's own API, account lifecycle, and settings persistence run for real against temporary data directories.
- The dashboard E2E tests (`tests/e2e/`) use Playwright with Chromium. Without Playwright installed they are skipped; `REQUIRE_E2E=1` turns that into a failure, as in CI.
- `coverage report` fails below the floor in `.coveragerc`. Raise that floor when coverage improves; never lower it.
- The native app's logic (connection rules, URLs, settings bridge, status parsing, menu bar text) has Swift tests: `swift test --package-path mac`.
- A native UI smoke test drives a separate test copy of the app (its own bundle id and settings) against a fake miner: `RUN_NATIVE_UI=1 ./venv/bin/python -m unittest tests.native.test_native_app -v`. It needs Accessibility permission for your terminal and runs only on demand, never in CI.
- The **Tests** workflow runs the Python suite on Python 3.10 and 3.12, and the Swift tests on macOS, for every push and pull request. Release tags run both again before packaging.
- Risk areas, priorities, and test conventions are recorded in `.agents/qa-project-context.md`.

---

### ⚙️ How It Works Under the Hood

Every several seconds, the application simulates watching an authorized stream by fetching stream metadata and playlist chunk headers. This advances Twitch drop progress without downloading video or audio data, saving bandwidth and system resources.

To maintain channel state (Online / Offline / Viewers), an asynchronous sharded WebSocket pool connects to Twitch PubSub Edge (`wss://pubsub-edge.twitch.tv/v1`), receiving real-time events for stream status changes, viewer counts, and drop progress completions. When a reward becomes claimable, the miner claims it if **Auto-claim drops** is enabled; otherwise it waits for manual claiming.
