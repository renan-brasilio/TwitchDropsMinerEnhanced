# Twitch Drops Miner Enhanced v1.0.1

This release brings major UI enhancements, customizable macOS menu bar display styles, a native `.dmg` drag-and-drop installer, visual game selection with live campaign counts, and image assets.

---

### 🌟 What's New in v1.0.1

#### 1. Customizable Menu Bar Display Styles
Configure exactly what appears in your top macOS menu bar in 1 click (from the menu bar dropdown or `Cmd+,` Connection Settings):
- **Icon Only** *(Default)*: Clean, compact template icon.
- **Icon + Current Game**: e.g., `[Icon] Overwatch`
- **Icon + Time Left**: e.g., `[Icon] 1h 45m`
- **Icon + Percentage**: e.g., `[Icon] 70%`
- **Icon + Full Status**: e.g., `[Icon] 70% Overwatch`
*Preferences automatically persist across application restarts in macOS `UserDefaults`.*

#### 2. Native macOS `.dmg` Installer
- **`TwitchDropsMinerEnhanced-macOS-arm64.dmg`**: Download, double-click, and drag directly into `/Applications`.
- **`TwitchDropsMinerEnhanced-macOS-arm64.zip`**: Portable archive option.

#### 3. Redesigned App Logo & Menu Bar Icons
- **Retina App Icon**: 1024×1024 squircle icon featuring the iconic Twitch chat bubble in vibrant purple with a metallic mining pickaxe across the front.
- **Menu Bar Template Icon**: Dedicated 18×18 and 36×36 (`@2x`) template icons that adapt automatically to macOS dark/light menu bars.

#### 4. Live Campaign Counts on Games
- Shows the number of active drop campaigns directly in:
  - The game selection dropdown (e.g. `Overwatch (2 active campaigns)`)
  - Quick-add chips (e.g. `+ Overwatch (2)`)
  - Ranked priority cards (`#1 Overwatch` &rarr; `2 active campaigns`)

#### 5. Dynamic Priority Picker Filtering
- Games already present in your priority list are **automatically filtered out** of the selection dropdown and quick-add chips to avoid duplicate clutter.
- Removing a game from priorities (`✕`) restores it to the picker immediately.
- Added a one-click `+ Prioritize` button directly to every campaign card in the gallery.

#### 6. Quick Copy for Device Activation Codes
- Added a one-click **Copy Code** button in the app banner and a **Copy Code** action in the macOS menu bar for rapid account pairing at `twitch.tv/activate`.

#### 7. Active Drops Campaigns Refresh
- Added an on-demand `POST /api/inventory/refresh` route. Clicking **Refresh** in the Campaigns tab actively re-queries Twitch GraphQL with live button loading states and toast notifications.

#### 8. Game Box Art & Item Reward Icons
- Displays official Twitch CDN game box art covers (44×58px) in campaign headers.
- Displays item reward thumbnail icons (34×34px) in drop sub-rows and the active target cockpit card.

---

### 🐛 Bug Fixes & Stability
- Fixed JavaScript syntax error in `web/index.html` preventing dashboard hydration in WebKit.
- Added immediate `pollFullState()` HTTP hydration on `DOMContentLoaded` with a 2.5s fallback loop.
- Disabled WebKit disk caching (`nonPersistent` data store + `no-cache` headers) ensuring fresh dashboard code is always loaded.
- Fixed premature API server shutdown in `main.py`.
- Fixed `InventoryHub.clear()` and multi-domain cookie lookup for SMARTBOX authorization.

---

### 📦 Installation (macOS Apple Silicon)

1. Download **`TwitchDropsMinerEnhanced-macOS-arm64.dmg`** below.
2. Open the `.dmg` and drag **`TwitchDropsMinerEnhanced.app`** into **Applications**.
3. Open from Applications or Spotlight.
