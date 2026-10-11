import Foundation
import Testing
@testable import MinerCore

private let miner = ConnectionSettings(isRemote: true, serverURL: "http://100.64.0.7:23432", apiKey: "s3cr3t-key-value")
private let minerPage = URL(string: "http://100.64.0.7:23432/?key=s3cr3t-key-value")
private let foreignPage = URL(string: "http://100.64.0.7:8080/")
private let app = NativeSettingsPayload.AppInfo(version: "1.8.0", autoCheckUpdates: true, update: .idle)

private func parse(_ body: Any, from url: URL? = minerPage, name: String = "settings", isMainFrame: Bool = true) -> BridgeMessage? {
    BridgeMessage.parse(name: name, isMainFrame: isMainFrame, body: body, frameURL: url, connection: miner)
}

@Suite("Settings bridge messages")
struct BridgeMessageTests {
    @Test("The offline page can always open the connection dialog")
    func offlineRecoveryIgnoresOrigin() {
        guard case .openConnectionSettings? = parse(["openConnectionSettings": true], from: nil) else {
            Issue.record("openConnectionSettings was ignored")
            return
        }
    }

    @Test("A connection change from another origin is ignored")
    func foreignConnectionChangeIgnored() {
        #expect(parse(["connection": ["mode": "local"]], from: foreignPage) == nil)
    }

    @Test("A preference change from another origin is ignored")
    func foreignPreferenceChangeIgnored() {
        #expect(parse(["keepRunningOnClose": false], from: foreignPage) == nil)
    }

    @Test("Messages from subframes, other handlers, or with non-object bodies are ignored")
    func malformedMessagesIgnored() {
        #expect(parse(["keepRunningOnClose": false], isMainFrame: false) == nil)
        #expect(parse(["keepRunningOnClose": false], name: "other") == nil)
        #expect(parse("keepRunningOnClose") == nil)
    }

    @Test("A connection change from the miner's page is forwarded")
    func minerConnectionChange() {
        guard case .connection(let request)? = parse(["connection": ["mode": "remote", "serverURL": "https://m.example.com"]]) else {
            Issue.record("connection change was not recognized")
            return
        }
        #expect(request["serverURL"] as? String == "https://m.example.com")
    }

    @Test("Preference changes apply valid values and drop invalid ones")
    func preferenceChanges() {
        guard case .preferences(let keep, let style, let autoCheck)? = parse(["keepRunningOnClose": false, "menuBarStyle": "timeLeft", "autoCheckUpdates": false]) else {
            Issue.record("preference change was not recognized")
            return
        }
        #expect(keep == false)
        #expect(style == .timeLeft)
        #expect(autoCheck == false)

        guard case .preferences(let badKeep, let badStyle, let badAutoCheck)? = parse(["keepRunningOnClose": "no", "menuBarStyle": "rainbow", "autoCheckUpdates": "yes"]) else {
            Issue.record("preference change was not recognized")
            return
        }
        #expect(badKeep == nil)
        #expect(badStyle == nil)
        #expect(badAutoCheck == nil)
    }

    @Test("Update requests are accepted only from the miner's page and only as check or install")
    func updateRequests() {
        guard case .update(.check)? = parse(["update": "check"]),
              case .update(.install)? = parse(["update": "install"]) else {
            Issue.record("update request was not recognized")
            return
        }
        #expect(parse(["update": "install"], from: foreignPage) == nil)
        #expect(parse(["update": "downgrade"]) == nil)
        #expect(parse(["update": true]) == nil)
    }
}

@Suite("native-settings payload")
struct NativeSettingsPayloadTests {
    @Test("The published settings never contain the API key, only whether one is saved")
    func neverLeaksAPIKey() throws {
        let payload = NativeSettingsPayload.make(keepRunningOnClose: true, menuBarStyle: .full, connection: miner, app: app,
                                                 error: "Enter a server URL starting with http:// or https://.")
        let script = try #require(NativeSettingsPayload.script(for: payload))
        #expect(!script.contains("s3cr3t-key-value"))
        let connection = try #require(payload["connection"] as? [String: Any])
        #expect(connection["hasApiKey"] as? Bool == true)
        #expect(Set(connection.keys) == ["mode", "serverURL", "hasApiKey"])
    }

    @Test("Remote mode publishes the miner URL; local mode publishes no URL")
    func connectionMode() throws {
        let remote = try #require(NativeSettingsPayload.make(keepRunningOnClose: true, menuBarStyle: .iconOnly, connection: miner, app: app)["connection"] as? [String: Any])
        #expect(remote["mode"] as? String == "remote")
        #expect(remote["serverURL"] as? String == "http://100.64.0.7:23432")

        let local = ConnectionSettings(isRemote: false, serverURL: "http://100.64.0.7:23432", apiKey: "")
        let localPayload = try #require(NativeSettingsPayload.make(keepRunningOnClose: false, menuBarStyle: .iconOnly, connection: local, app: app)["connection"] as? [String: Any])
        #expect(localPayload["mode"] as? String == "local")
        #expect(localPayload["serverURL"] as? String == "")
        #expect(localPayload["hasApiKey"] as? Bool == false)
    }

    @Test("The script publishes the payload as window.nativeSettings and a native-settings event")
    func scriptDispatchesEvent() throws {
        let payload = NativeSettingsPayload.make(keepRunningOnClose: false, menuBarStyle: .percentage, connection: miner, app: app, error: "boom")
        let script = try #require(NativeSettingsPayload.script(for: payload))
        let prefix = "window.nativeSettings = "
        let start = try #require(script.range(of: prefix)).upperBound
        let end = try #require(script.range(of: ";\n", range: start..<script.endIndex)).lowerBound
        let published = try #require(JSONSerialization.jsonObject(with: Data(script[start..<end].utf8)) as? [String: Any])
        #expect(published["keepRunningOnClose"] as? Bool == false)
        #expect(published["menuBarStyle"] as? String == "percentage")
        #expect(published["error"] as? String == "boom")
        #expect(script.contains("new CustomEvent('native-settings', {detail: window.nativeSettings})"))
    }

    @Test("The app section carries the app version, auto-check preference and update state")
    func appSection() throws {
        let release = ReleaseInfo(version: "1.9.0", pageURL: URL(string: "https://github.com/o/r/releases/tag/v1.9.0")!,
                                  archiveURL: URL(string: "https://github.com/o/r/a.zip")!, sha256: String(repeating: "a", count: 64))
        let info = NativeSettingsPayload.AppInfo(version: "1.8.0", autoCheckUpdates: false, update: .available(release))
        let section = try #require(NativeSettingsPayload.make(keepRunningOnClose: true, menuBarStyle: .full, connection: miner, app: info)["app"] as? [String: Any])
        #expect(section["version"] as? String == "1.8.0")
        #expect(section["autoCheckUpdates"] as? Bool == false)
        let update = try #require(section["update"] as? [String: Any])
        #expect(update["state"] as? String == "available")
        #expect(update["latestVersion"] as? String == "1.9.0")
        #expect(update["releaseURL"] as? String == "https://github.com/o/r/releases/tag/v1.9.0")
    }
}
