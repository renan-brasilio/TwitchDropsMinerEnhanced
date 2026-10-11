import Foundation

/// Where the app finds its miner: the local daemon or a remote server, plus the optional API key.
/// `serverURL` is the raw stored value; `effectiveURL` is what the app actually talks to.
struct ConnectionSettings: Equatable {
    static let localServerURL = "http://127.0.0.1:23432"

    var isRemote: Bool
    var serverURL: String
    var apiKey: String

    init(isRemote: Bool, serverURL: String?, apiKey: String?) {
        self.isRemote = isRemote
        let stored = serverURL ?? ""
        self.serverURL = stored.isEmpty ? ConnectionSettings.localServerURL : stored
        self.apiKey = apiKey ?? ""
    }

    var effectiveURL: URL {
        let fallback = URL(string: ConnectionSettings.localServerURL)!
        let trimmed = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.hasPrefix("http://") || trimmed.hasPrefix("https://") {
            return URL(string: trimmed) ?? fallback
        }
        return URL(string: "http://\(trimmed)") ?? fallback
    }

    /// `Authorization` header value for REST requests, when a key is saved.
    var authorizationHeader: String? {
        apiKey.isEmpty ? nil : "Bearer \(apiKey)"
    }

    /// Dashboard page URL; the key rides in the `key` query so the page's own API calls authenticate.
    var dashboardURL: URL {
        let base = effectiveURL
        guard !apiKey.isEmpty else { return base }
        var comps = URLComponents(url: base, resolvingAgainstBaseURL: false)
        var items = comps?.percentEncodedQueryItems ?? []
        items.append(Self.encodedKeyItem(apiKey))
        comps?.percentEncodedQueryItems = items
        return comps?.url ?? base
    }

    /// Live status WebSocket (`/ws`), `wss` for an https miner.
    var webSocketURL: URL? {
        let base = effectiveURL
        var comps = URLComponents(url: base, resolvingAgainstBaseURL: false)
        comps?.scheme = (base.scheme == "https") ? "wss" : "ws"
        comps?.path = "/ws"
        if !apiKey.isEmpty {
            comps?.percentEncodedQueryItems = [Self.encodedKeyItem(apiKey)]
        }
        return comps?.url
    }

    /// URLComponents leaves '+' unescaped, but servers and URLSearchParams decode it as a space.
    private static func encodedKeyItem(_ key: String) -> URLQueryItem {
        var allowed = CharacterSet.urlQueryAllowed
        allowed.remove(charactersIn: "+&=#")
        return URLQueryItem(name: "key", value: key.addingPercentEncoding(withAllowedCharacters: allowed) ?? key)
    }

    /// REST endpoint such as `/api/status` on the miner.
    func apiURL(_ endpoint: String) -> URL? {
        URL(string: "\(effectiveURL.absoluteString)\(endpoint)")
    }

    /// Only pages served by the configured miner may change native settings.
    func isMinerOrigin(_ url: URL?) -> Bool {
        guard let url else { return false }
        let miner = effectiveURL
        return url.scheme == miner.scheme && url.host == miner.host && url.port == miner.port
    }

    /// Short connection label used in the window title.
    var windowModeText: String {
        isRemote ? "Remote [\(effectiveURL.host ?? "VPS")]" : "Local"
    }

    /// Applies a `connection` request from the dashboard's Settings page.
    /// Local always targets the local daemon. Remote needs an http(s) URL with a host.
    /// An absent `apiKey` keeps the saved key; a string (even empty) replaces it.
    func applying(_ request: [String: Any]) -> Result<ConnectionSettings, ConnectionSettingsError> {
        guard let mode = request["mode"] as? String, mode == "local" || mode == "remote" else {
            return .failure(.invalidMode)
        }
        let remote = mode == "remote"
        var newURL = ConnectionSettings.localServerURL
        if remote {
            let raw = (request["serverURL"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
            guard let parsed = URL(string: raw), ["http", "https"].contains(parsed.scheme ?? ""),
                  let host = parsed.host, !host.isEmpty else {
                return .failure(.invalidServerURL)
            }
            newURL = raw
        }
        var updated = self
        updated.isRemote = remote
        updated.serverURL = newURL
        if let key = request["apiKey"] as? String {
            updated.apiKey = key.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return .success(updated)
    }

    /// Values saved by the native "Connection Settings…" dialog (fields are submitted as shown).
    static func fromConnectionDialog(remote: Bool, serverURL: String, apiKey: String) -> ConnectionSettings {
        let url = serverURL.trimmingCharacters(in: .whitespacesAndNewlines)
        // Local always means this Mac's daemon, whatever is left in the URL field.
        return ConnectionSettings(
            isRemote: remote,
            serverURL: remote ? (url.isEmpty ? "http://vps:23432" : url) : localServerURL,
            apiKey: apiKey.trimmingCharacters(in: .whitespacesAndNewlines)
        )
    }
}

enum ConnectionSettingsError: Error, Equatable {
    case invalidMode
    case invalidServerURL

    /// Shown on the dashboard's Settings page.
    var message: String {
        switch self {
        case .invalidMode: return "Choose a local or remote miner."
        case .invalidServerURL: return "Enter a server URL starting with http:// or https://."
        }
    }
}
