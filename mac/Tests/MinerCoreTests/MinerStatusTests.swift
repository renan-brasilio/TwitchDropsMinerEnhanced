import Foundation
import Testing
@testable import MinerCore

/// Parses JSON text the way the app receives it (JSONSerialization: NSNumber, NSNull).
private func object(_ json: String) -> [String: Any] {
    (try? JSONSerialization.jsonObject(with: Data(json.utf8))) as? [String: Any] ?? [:]
}

/// `/api/status` while watching, shaped like the backend's `StateHub` snapshot.
private let watchingSnapshot = """
{"state": "CHANNEL_SWITCH", "status_text": "Watching: e2e_streamer", "is_logged_in": true, "username": "e2e_viewer",
 "active_channel": {"id": 7001, "name": "e2e_streamer", "viewers": 1234, "online": true, "game": "E2E Quest"},
 "active_drop": {"id": "e2e-drop", "name": "E2E Reward", "game": "E2E Quest", "current_minutes": 20,
                 "required_minutes": 60, "remaining_minutes": 40, "percentage": 33.3, "seconds": 60},
 "device_code": null}
"""

private func watching() -> MinerStatus {
    var status = MinerStatus()
    status.apply(snapshot: object(watchingSnapshot))
    return status
}

@Suite("Status snapshot parsing")
struct StatusSnapshotTests {
    @Test("A full snapshot fills the drop, channel, and status")
    func fullSnapshot() {
        let status = watching()
        #expect(status.minerState == "CHANNEL_SWITCH")
        #expect(status.statusText == "Watching: e2e_streamer")
        #expect(status.activeDropName == "E2E Reward")
        #expect(status.activeGame == "E2E Quest")
        #expect(status.activeDropPercent == 33.3)
        #expect(status.activeDropMinutes == "20/60m")
        #expect(status.activeDropRemaining == 40)
        #expect(status.activeChannel == "e2e_streamer")
        #expect(status.activeViewers == 1234)
        #expect(status.deviceCode == nil)
    }

    @Test("Missing or null fields fall back to idle with nothing active")
    func missingAndNullFields() {
        var status = watching()
        status.apply(snapshot: object(#"{"state": null, "active_drop": null, "active_channel": null, "device_code": null}"#))
        #expect(status.minerState == "IDLE")
        #expect(status.statusText == "Idle")
        #expect(status.activeDropName == nil)
        #expect(status.activeGame == nil)
        #expect(status.activeDropPercent == 0)
        #expect(status.activeDropMinutes == nil)
        #expect(status.activeChannel == nil)
        #expect(status.activeViewers == 0)
    }

    @Test("A drop with missing progress fields shows 0 of 60 minutes")
    func dropWithMissingFields() {
        var status = MinerStatus()
        status.apply(snapshot: object(#"{"active_drop": {"name": "Reward", "percentage": null}}"#))
        #expect(status.activeDropName == "Reward")
        #expect(status.activeDropPercent == 0)
        #expect(status.activeDropMinutes == "0/60m")
        #expect(status.activeDropRemaining == 0)
    }

    @Test("Whole-number percentages from JSON are read")
    func integerPercentage() {
        var status = MinerStatus()
        status.apply(snapshot: object(#"{"active_drop": {"name": "Reward", "game": "G", "percentage": 50}}"#))
        #expect(status.activeDropPercent == 50)
    }

    @Test("A pending device code exposes the activation code and URL")
    func deviceCode() {
        var status = MinerStatus()
        status.apply(snapshot: object(#"{"device_code": {"code": "ABCD-1234", "uri": "https://www.twitch.tv/activate"}}"#))
        #expect(status.deviceCode == "ABCD-1234")
        #expect(status.deviceUri == "https://www.twitch.tv/activate")
        status.apply(snapshot: object(#"{"device_code": null}"#))
        #expect(status.deviceCode == nil)
    }
}

@Suite("WebSocket events")
struct WebSocketEventTests {
    private func send(_ text: String, to status: inout MinerStatus) -> Bool {
        guard let message = MinerStatus.decodeWebSocketMessage(text) else { return false }
        return status.apply(event: message.event, payload: message.payload)
    }

    @Test("init replaces the whole state")
    func initEvent() {
        var status = MinerStatus()
        #expect(send(#"{"event": "init", "data": \#(watchingSnapshot)}"#, to: &status))
        #expect(status == watching())
    }

    @Test("status updates the text and state, keeping fields it omits")
    func statusEvent() {
        var status = watching()
        #expect(send(#"{"event": "status", "data": {"status": "Paused by user", "state": "IDLE"}}"#, to: &status))
        #expect(status.statusText == "Paused by user")
        #expect(status.minerState == "IDLE")
        #expect(send(#"{"event": "status", "data": {"status": "Resuming mining..."}}"#, to: &status))
        #expect(status.minerState == "IDLE")
        #expect(send(#"{"event": "status", "data": {"state": "INVENTORY_FETCH"}}"#, to: &status))
        #expect(status.statusText == "Resuming mining...")
        #expect(status.activeDropName == "E2E Reward")
    }

    @Test("drop null clears the active drop")
    func dropCleared() {
        var status = watching()
        #expect(send(#"{"event": "drop", "data": null}"#, to: &status))
        #expect(status.activeDropName == nil)
        #expect(status.activeGame == nil)
        #expect(status.activeDropPercent == 0)
        #expect(status.activeChannel == "e2e_streamer")
    }

    @Test("Clearing the drop leaves no time left in the menu bar")
    func dropClearedResetsRemaining() {
        var status = watching()
        _ = send(#"{"event": "drop", "data": null}"#, to: &status)
        #expect(status.activeDropRemaining == 0)
        #expect(StatusPresentation.menuBarTitle(style: .timeLeft, status: status) == "")
    }

    @Test("watching null clears the channel")
    func watchingCleared() {
        var status = watching()
        #expect(send(#"{"event": "watching", "data": null}"#, to: &status))
        #expect(status.activeChannel == nil)
        #expect(status.activeViewers == 0)
        #expect(send(#"{"event": "watching", "data": {"name": "other", "viewers": 7}}"#, to: &status))
        #expect(status.activeChannel == "other")
        #expect(status.activeViewers == 7)
    }

    @Test("Unknown events, malformed payloads, and invalid frames change nothing",
          arguments: [#"{"event": "channels", "data": []}"#, #"{"event": "init", "data": null}"#,
                      #"{"event": "status", "data": "Idle"}"#, #"{"data": {}}"#, "not json", "[1, 2]"])
    func ignoredMessages(text: String) {
        var status = watching()
        #expect(!send(text, to: &status))
        #expect(status == watching())
    }
}
