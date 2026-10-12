# v1.8.3

## Fix: Account setup & unauthenticated settings controls

- **Browser auth-token input and "Save token & connect" button now enable immediately** when opening the app without an account, instead of remaining permanently disabled.
- **"Disconnect" button is now hidden** when no account is connected (it only appeared disabled, confusing users).
- **"Connect with Twitch" button works directly** and now links directly to the Twitch device activation page (`twitch.tv/activate`) with the generated activation code right in the Settings tab, complete with a one-click **Copy Code** and **Open Activation Page** button.
- **"Automatically claim ready drops" toggle is enabled** and configurable even if the user hasn't finished connecting their Twitch account yet.

## Downloads

- `TwitchDropsMinerEnhanced-v1.8.3-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.8.3-macOS-arm64.zip`: macOS application archive (the in-app updater installs from this file).
