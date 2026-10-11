import Foundation

/// The miner state shown by the status item, its menu, and the window title.
/// Fed by `/api/status` snapshots and `/ws` events.
struct MinerStatus: Equatable {
    var minerState: String = "CONNECTING"
    var statusText: String = "Connecting..."
    var activeGame: String? = nil
    var activeDropName: String? = nil
    var activeDropPercent: Double = 0.0
    var activeDropMinutes: String? = nil
    var activeDropRemaining: Int = 0
    var activeChannel: String? = nil
    var activeViewers: Int = 0
    var deviceCode: String? = nil
    var deviceUri: String? = nil

    /// Replaces everything from a full `/api/status` (or WebSocket `init`) snapshot.
    mutating func apply(snapshot json: [String: Any]) {
        minerState = json["state"] as? String ?? "IDLE"
        statusText = json["status_text"] as? String ?? "Idle"
        apply(drop: json["active_drop"])
        apply(channel: json["active_channel"])
        if let dc = json["device_code"] as? [String: Any] {
            deviceCode = dc["code"] as? String
            deviceUri = dc["uri"] as? String
        } else {
            deviceCode = nil
            deviceUri = nil
        }
    }

    /// Applies one decoded `/ws` message. Returns false for events the app does not track.
    mutating func apply(event: String, payload: Any?) -> Bool {
        switch event {
        case "init":
            guard let state = payload as? [String: Any] else { return false }
            apply(snapshot: state)
        case "status":
            guard let s = payload as? [String: Any] else { return false }
            statusText = s["status"] as? String ?? statusText
            minerState = s["state"] as? String ?? minerState
        case "drop":
            apply(drop: payload)
        case "watching":
            apply(channel: payload)
        default:
            return false
        }
        return true
    }

    /// Decodes a `/ws` text frame into its event name and payload.
    static func decodeWebSocketMessage(_ text: String) -> (event: String, payload: Any?)? {
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let event = json["event"] as? String else { return nil }
        return (event, json["data"])
    }

    private mutating func apply(drop payload: Any?) {
        if let drop = payload as? [String: Any] {
            activeDropName = drop["name"] as? String
            activeGame = drop["game"] as? String
            activeDropPercent = drop["percentage"] as? Double ?? 0.0
            let curr = drop["current_minutes"] as? Int ?? 0
            let req = drop["required_minutes"] as? Int ?? 60
            activeDropRemaining = drop["remaining_minutes"] as? Int ?? 0
            activeDropMinutes = "\(curr)/\(req)m"
        } else {
            activeDropName = nil
            activeGame = nil
            activeDropPercent = 0.0
            activeDropMinutes = nil
            activeDropRemaining = 0
        }
    }

    private mutating func apply(channel payload: Any?) {
        if let ch = payload as? [String: Any] {
            activeChannel = ch["name"] as? String
            activeViewers = ch["viewers"] as? Int ?? 0
        } else {
            activeChannel = nil
            activeViewers = 0
        }
    }
}
