# Twitch Drops Miner Enhanced v1.0.0

A modern, high-performance refactor of DevilXD's TwitchDropsMiner, redesigned specifically for macOS and modern systems.

### 🚀 Key Features

- **🍎 Full Native macOS App Window + Menu Bar Status Extra**:
  - Resizable native application window (`WKWebView`) with live campaign progress, drop rewards, and channel metrics.
  - Compact menu bar template icon with a rich click-dropdown cockpit menu.
  - Closing the window hides it while keeping background mining active in the menu bar.
  - Native macOS notifications when drops are claimed.
- **🎨 Custom Retina Icon**:
  - 1024x1024 squircle app icon featuring the Twitch chat bubble in brand purple with a titanium pickaxe.
  - Dedicated 18x18 and 36x36 (@2x) template icons for the macOS menu bar.
- **⚡ Zero-Lag Decoupled Architecture**:
  - Removed the blocking 50-100Hz Tkinter main-thread polling loop that caused UI stutter, beachballs, and high CPU usage on macOS.
  - Operates with 0.0% idle CPU usage on an asynchronous event-driven hub.
- **🔐 Modern Authentication**:
  - Fixed `KeyError: 'device_code'` by switching to Twitch's active SMARTBOX client for device activation.
  - Quick-copy button for the activation code directly in the app banner and menu bar.
  - Direct browser `auth-token` cookie login for 100% captcha-free authentication.
- **🎮 Visual Game Priorities Manager**:
  - Interactive game selection dropdown combining active campaign games and popular Twitch drops titles.
  - Quick-add chips for top titles.
  - Ranked priority cards with rank numbers, reordering (▲ / ▼), removal (✕), and live campaign counts.
  - One-click `+ Prioritize` button on every campaign card.
- **🐳 Docker & Remote VPS Support (WIP)**:
  - Multi-stage Dockerfile and Docker Compose configuration for 24/7 VPS deployment.
  - Switch between Local Daemon and Remote VPS in Connection Settings (`Cmd+,`).
  - Optional `API_KEY` Bearer token protection for public hosting.

### 📦 Installation (macOS Apple Silicon)

1. Download **`TwitchDropsMinerEnhanced-macOS-arm64.zip`** from the release assets.
2. Unzip and drag **`TwitchDropsMinerEnhanced.app`** to your Applications folder.
3. Open the app and log in via device code or your browser `auth-token` cookie.
