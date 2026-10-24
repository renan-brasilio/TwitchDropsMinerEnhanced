# v1.8.7

## Fix: App stays running in menu bar when window is closed

- **Disabled macOS Automatic Termination**: When closing the window to the menu bar (`keepRunningOnClose: true`), macOS AppKit previously treated the app as having no open windows and automatically sent a Quit AppleEvent (`TAL` termination) after a short delay.
- **Added `NSSupportsAutomaticTermination: false`** to `Info.plist` and invoked `ProcessInfo.processInfo.disableAutomaticTermination(...)`.
- **Implemented `applicationShouldTerminate` & `applicationShouldTerminateAfterLastWindowClosed`**: Cancel background termination unless the user explicitly chose **Quit** (`Cmd+Q`) from the menu bar or an in-app update is restarting the app.

## Downloads

- `TwitchDropsMinerEnhanced-v1.8.7-macOS-arm64.dmg`: drag-and-drop macOS installer.
- `TwitchDropsMinerEnhanced-v1.8.7-macOS-arm64.zip`: macOS application archive (the in-app updater installs from this file).
