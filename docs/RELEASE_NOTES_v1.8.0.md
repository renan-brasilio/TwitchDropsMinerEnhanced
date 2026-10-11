# v1.8.0

## New: About and in-app updates

- **Settings → About** shows the miner's version and where it runs (this Mac or your server), with links to the GitHub repository, releases and issues. In the Mac app it also shows the app's own version.
- **Updates from GitHub Releases.** The Mac app checks for a newer release at launch and once a day; the daily check can be turned off in About. You can also check any time with **Check for Updates…** in the app menu, the menu bar menu, or About.
- **Install and restart** downloads the release's `.zip` and verifies it against the SHA-256 checksum GitHub publishes for it. It also checks that the archive contains this app at the announced version. The app then quits, swaps itself in place and relaunches. If the copy fails, the previous app is put back. Nothing is installed without your click.
- The app update doesn't update the miner. About tells you when the miner (for example your Docker server) is older than the latest release.
- **About Twitch Drops Miner Enhanced** in the app menu now opens this section.

This is the first version with the updater, so moving to v1.8.0 itself is a manual download. Later versions install from inside the app.

## Tests

New tests cover reading GitHub's release data, comparing versions, installing from a real archive (and refusing one with a wrong checksum, another app, or another version), putting the previous app back when the copy fails, the update menu item, and the About section in the browser.

## Downloads

- `TwitchDropsMinerEnhanced-v1.8.0-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.8.0-macOS-arm64.zip`: macOS application archive (the updater installs from this file).
