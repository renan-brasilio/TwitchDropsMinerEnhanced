import Foundation
import Testing
@testable import MinerCore

private let remoteMiner = ConnectionSettings(isRemote: true, serverURL: "https://miner.example.com:8443", apiKey: "saved-key")
private let localMiner = ConnectionSettings(isRemote: false, serverURL: nil, apiKey: nil)

private func applied(_ request: [String: Any], to current: ConnectionSettings = remoteMiner) throws -> ConnectionSettings {
    try current.applying(request).get()
}

/// Decodes the `key` query parameter the way the miner (yarl) and the dashboard (URLSearchParams) do.
private func formDecodedKey(_ url: URL?) -> String? {
    guard let query = url.flatMap({ URLComponents(url: $0, resolvingAgainstBaseURL: false) })?.percentEncodedQuery else {
        return nil
    }
    for pair in query.split(separator: "&") {
        let parts = pair.split(separator: "=", maxSplits: 1).map(String.init)
        if parts.first == "key" {
            return parts.count > 1 ? parts[1].replacingOccurrences(of: "+", with: " ").removingPercentEncoding : ""
        }
    }
    return nil
}

@Suite("Connection settings from the dashboard")
struct ConnectionChangeTests {
    @Test("Remote mode rejects a missing, blank, or non-http(s) server URL",
          arguments: [nil, "", "   ", "not a url", "ftp://miner.example.com", "http://", "http://:23432", "file:///etc/passwd", "miner.example.com:23432"])
    func remoteRejectsInvalidURL(serverURL: String?) {
        var request: [String: Any] = ["mode": "remote", "apiKey": "new-key"]
        request["serverURL"] = serverURL
        let result = localMiner.applying(request)
        #expect(throws: ConnectionSettingsError.invalidServerURL) { try result.get() }
    }

    @Test("A missing or unknown mode is rejected", arguments: [nil, "cloud", "REMOTE"])
    func rejectsUnknownMode(mode: String?) {
        var request: [String: Any] = ["serverURL": "https://miner.example.com"]
        request["mode"] = mode
        #expect(throws: ConnectionSettingsError.invalidMode) { try remoteMiner.applying(request).get() }
    }

    @Test("Remote mode saves the trimmed server URL")
    func remoteSavesTrimmedURL() throws {
        let updated = try applied(["mode": "remote", "serverURL": "  http://100.64.0.7:23432 \n"], to: localMiner)
        #expect(updated.isRemote)
        #expect(updated.serverURL == "http://100.64.0.7:23432")
        #expect(updated.effectiveURL.absoluteString == "http://100.64.0.7:23432")
    }

    @Test("Local mode always targets 127.0.0.1:23432, ignoring any submitted URL")
    func localForcesLocalDaemon() throws {
        let updated = try applied(["mode": "local", "serverURL": "https://evil.example.com"])
        #expect(!updated.isRemote)
        #expect(updated.effectiveURL.absoluteString == "http://127.0.0.1:23432")
    }

    @Test("An absent API key keeps the saved key")
    func absentKeyKeepsSavedKey() throws {
        let updated = try applied(["mode": "remote", "serverURL": "https://other.example.com"])
        #expect(updated.apiKey == "saved-key")
    }

    @Test("An empty API key removes the saved key")
    func emptyKeyClearsSavedKey() throws {
        let updated = try applied(["mode": "remote", "serverURL": "https://miner.example.com:8443", "apiKey": ""])
        #expect(updated.apiKey.isEmpty)
        #expect(updated.authorizationHeader == nil)
    }

    @Test("A new API key replaces the saved key, trimmed")
    func newKeyReplacesSavedKey() throws {
        let updated = try applied(["mode": "remote", "serverURL": "https://miner.example.com:8443", "apiKey": "  fresh-key \n"])
        #expect(updated.apiKey == "fresh-key")
        #expect(updated.authorizationHeader == "Bearer fresh-key")
    }
}

@Suite("Native connection dialog")
struct ConnectionDialogTests {
    @Test("Saving Local in the dialog targets the local daemon even if the remote URL is still in the field")
    func dialogLocalForcesLocalDaemon() {
        let saved = ConnectionSettings.fromConnectionDialog(remote: false, serverURL: "https://miner.example.com", apiKey: "")
        #expect(!saved.isRemote)
        #expect(saved.effectiveURL.absoluteString == "http://127.0.0.1:23432")
    }

    @Test("Saving with an empty key field removes the key; a filled field is trimmed")
    func dialogKeyField() {
        #expect(ConnectionSettings.fromConnectionDialog(remote: true, serverURL: "https://m.example.com", apiKey: "  ").apiKey.isEmpty)
        #expect(ConnectionSettings.fromConnectionDialog(remote: true, serverURL: "https://m.example.com", apiKey: " k ").apiKey == "k")
    }
}

@Suite("Miner URLs")
struct MinerURLTests {
    @Test("No saved server URL means the local daemon")
    func defaultsToLocalDaemon() {
        #expect(localMiner.effectiveURL.absoluteString == "http://127.0.0.1:23432")
        #expect(ConnectionSettings(isRemote: false, serverURL: "", apiKey: "").effectiveURL.absoluteString == "http://127.0.0.1:23432")
    }

    @Test("A saved URL without a scheme is reached over http")
    func schemelessURLUsesHTTP() {
        let settings = ConnectionSettings(isRemote: true, serverURL: " vps.example.com:23432 ", apiKey: nil)
        #expect(settings.effectiveURL.absoluteString == "http://vps.example.com:23432")
    }

    @Test("The dashboard URL carries the saved key in the `key` query")
    func dashboardURLCarriesKey() {
        #expect(remoteMiner.dashboardURL.absoluteString == "https://miner.example.com:8443?key=saved-key")
        #expect(localMiner.dashboardURL.absoluteString == "http://127.0.0.1:23432")
    }

    @Test("The WebSocket URL is /ws over ws or wss and carries the saved key")
    func webSocketURL() {
        #expect(remoteMiner.webSocketURL?.absoluteString == "wss://miner.example.com:8443/ws?key=saved-key")
        #expect(localMiner.webSocketURL?.absoluteString == "ws://127.0.0.1:23432/ws")
    }

    @Test("REST endpoints are resolved against the miner and authenticated with a Bearer header")
    func restEndpoints() {
        #expect(remoteMiner.apiURL("/api/status")?.absoluteString == "https://miner.example.com:8443/api/status")
        #expect(remoteMiner.authorizationHeader == "Bearer saved-key")
        #expect(localMiner.authorizationHeader == nil)
    }

    @Test("Keys with URL delimiters reach the miner intact", arguments: ["a&b=c", "k#frag?x", "with space"])
    func delimiterKeysRoundTrip(key: String) {
        let settings = ConnectionSettings(isRemote: true, serverURL: "https://miner.example.com", apiKey: key)
        #expect(formDecodedKey(settings.dashboardURL) == key)
        #expect(formDecodedKey(settings.webSocketURL) == key)
    }

    @Test("Keys containing '+' (e.g. base64-generated) reach the miner intact")
    func plusKeysRoundTrip() {
        let settings = ConnectionSettings(isRemote: true, serverURL: "https://miner.example.com", apiKey: "q1w+e2/r3==")
        #expect(formDecodedKey(settings.dashboardURL) == "q1w+e2/r3==")
        #expect(formDecodedKey(settings.webSocketURL) == "q1w+e2/r3==")
    }

    @Test("Window title shows the remote host, or Local")
    func windowModeText() {
        #expect(remoteMiner.windowModeText == "Remote [miner.example.com]")
        #expect(localMiner.windowModeText == "Local")
    }
}

@Suite("Settings bridge origin check")
struct MinerOriginTests {
    @Test("Pages from the configured miner are trusted regardless of path or query",
          arguments: ["https://miner.example.com:8443", "https://miner.example.com:8443/?key=saved-key", "https://miner.example.com:8443/index.html#settings"])
    func acceptsMinerPages(url: String) {
        #expect(remoteMiner.isMinerOrigin(URL(string: url)))
    }

    @Test("Other hosts, ports, and schemes are rejected",
          arguments: ["https://evil.example.com:8443", "https://miner.example.com:9443", "http://miner.example.com:8443",
                      "https://miner.example.com", "https://miner.example.com.evil.net:8443", "about:blank"])
    func rejectsOtherOrigins(url: String) {
        #expect(!remoteMiner.isMinerOrigin(URL(string: url)))
    }

    @Test("A frame without a URL is rejected")
    func rejectsMissingURL() {
        #expect(!localMiner.isMinerOrigin(nil))
    }
}
