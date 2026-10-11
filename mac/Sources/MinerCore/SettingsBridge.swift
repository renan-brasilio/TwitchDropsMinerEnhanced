import Foundation

/// A message the dashboard posts to `window.webkit.messageHandlers.settings`.
enum BridgeMessage {
    /// The offline page asks for the native connection dialog.
    case openConnectionSettings
    /// The Settings page switches the miner connection.
    case connection([String: Any])
    /// The Settings page changes app preferences; absent or invalid values leave them unchanged.
    case preferences(keepRunningOnClose: Bool?, menuBarStyle: MenuBarStyle?, autoCheckUpdates: Bool?)
    /// Settings → About asks to check for, or install, an app update.
    case update(UpdateAction)

    enum UpdateAction: String {
        case check, install
    }

    /// Decodes a script message, or returns nil when it must be ignored.
    /// Only the offline recovery request is accepted from outside the miner's origin (that page is
    /// local HTML); everything else must come from the main frame of the configured miner.
    static func parse(name: String, isMainFrame: Bool, body: Any, frameURL: URL?,
                      connection: ConnectionSettings) -> BridgeMessage? {
        guard name == "settings", isMainFrame, let settings = body as? [String: Any] else { return nil }
        if settings["openConnectionSettings"] as? Bool == true {
            return .openConnectionSettings
        }
        guard connection.isMinerOrigin(frameURL) else { return nil }
        if let request = settings["connection"] as? [String: Any] {
            return .connection(request)
        }
        if let raw = settings["update"] {
            return (raw as? String).flatMap(UpdateAction.init(rawValue:)).map(BridgeMessage.update)
        }
        let style = (settings["menuBarStyle"] as? String).flatMap(MenuBarStyle.init(rawValue:))
        return .preferences(keepRunningOnClose: settings["keepRunningOnClose"] as? Bool, menuBarStyle: style,
                            autoCheckUpdates: settings["autoCheckUpdates"] as? Bool)
    }
}

/// The `native-settings` event the app publishes to the dashboard.
enum NativeSettingsPayload {
    /// The API key never leaves native storage; the page only learns whether one is saved.
    static func make(keepRunningOnClose: Bool, menuBarStyle: MenuBarStyle,
                     connection: ConnectionSettings, app: AppInfo, error: String? = nil) -> [String: Any] {
        var settings: [String: Any] = [
            "keepRunningOnClose": keepRunningOnClose,
            "menuBarStyle": menuBarStyle.rawValue,
            "connection": [
                "mode": connection.isRemote ? "remote" : "local",
                "serverURL": connection.isRemote ? connection.effectiveURL.absoluteString : "",
                "hasApiKey": !connection.apiKey.isEmpty
            ] as [String: Any],
            "app": [
                "version": app.version,
                "autoCheckUpdates": app.autoCheckUpdates,
                "update": app.update.payload
            ] as [String: Any]
        ]
        if let error { settings["error"] = error }
        return settings
    }

    /// The Mac app's own version and update state (the miner reports its version separately).
    struct AppInfo {
        let version: String
        let autoCheckUpdates: Bool
        let update: UpdateState
    }

    /// JavaScript that stores the payload in `window.nativeSettings` and dispatches the event.
    static func script(for settings: [String: Any]) -> String? {
        guard let data = try? JSONSerialization.data(withJSONObject: settings),
              let json = String(data: data, encoding: .utf8) else { return nil }
        return """
            window.nativeSettings = \(json);
            window.dispatchEvent(new CustomEvent('native-settings', {detail: window.nativeSettings}));
            """
    }
}
