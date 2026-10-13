# v1.8.6

## Fix: Restored stylesheet parsing

- **Fixed missing closing brace in `<style>` block**: A missing brace on `.cockpit-body.is-empty` caused the CSS parser to fail on all subsequent rules, breaking the metric cards, tab icons, and tables. All styles are now completely restored.

## Downloads

- `TwitchDropsMinerEnhanced-v1.8.6-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.8.6-macOS-arm64.zip`: macOS application archive (the in-app updater installs from this file).
