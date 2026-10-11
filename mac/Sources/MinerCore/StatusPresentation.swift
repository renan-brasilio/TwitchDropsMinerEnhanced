import Foundation

enum MenuBarStyle: String, CaseIterable {
    case iconOnly = "iconOnly"
    case currentMining = "currentMining"
    case timeLeft = "timeLeft"
    case percentage = "percentage"
    case full = "full"

    var title: String {
        switch self {
        case .iconOnly: return "Icon Only"
        case .currentMining: return "Icon + Current Game (e.g. Overwatch)"
        case .timeLeft: return "Icon + Time Left (e.g. 35m)"
        case .percentage: return "Icon + Percentage (e.g. 65%)"
        case .full: return "Icon + Full Status (e.g. 65% Overwatch)"
        }
    }
}

/// Text for the status item, its menu, and the main window, derived from `MinerStatus`.
enum StatusPresentation {
    /// Status item title next to the icon; empty means icon only.
    static func menuBarTitle(style: MenuBarStyle, status: MinerStatus) -> String {
        let idle = status.minerState == "IDLE" ? " Idle" : ""
        switch style {
        case .iconOnly:
            return ""
        case .currentMining:
            if let game = status.activeGame { return " \(game)" }
            return idle
        case .timeLeft:
            return status.activeDropRemaining > 0 ? " \(formatMinutes(status.activeDropRemaining))" : ""
        case .percentage:
            return status.activeDropPercent > 0 ? " \(Int(status.activeDropPercent))%" : ""
        case .full:
            if let game = status.activeGame, status.activeDropPercent > 0 {
                return " \(Int(status.activeDropPercent))% \(game)"
            }
            return idle
        }
    }

    static func toolTip(status: MinerStatus) -> String {
        if let game = status.activeGame, status.activeDropPercent > 0 {
            let timeStr = formatMinutes(status.activeDropRemaining)
            return "Twitch Drops Miner Enhanced — \(game): \(Int(status.activeDropPercent))% (\(timeStr) remaining)"
        }
        return "Twitch Drops Miner Enhanced — \(status.statusText)"
    }

    static func windowTitle(status: MinerStatus, connection: ConnectionSettings) -> String {
        "Twitch Drops Miner Enhanced — \(status.statusText) (\(connection.windowModeText))"
    }

    /// "Status: …" row of the status item menu.
    static func statusLine(status: MinerStatus, isRemote: Bool) -> String {
        let statusDot: String
        switch status.minerState {
        case "IDLE": statusDot = "○ Idle"
        case "OFFLINE": statusDot = "■ Offline"
        case "CHANNEL_SWITCH", "CHANNELS_FETCH", "INVENTORY_FETCH": statusDot = "↻ Syncing"
        default: statusDot = "● Active"
        }
        return "Status: \(statusDot) \(isRemote ? "[Remote]" : "[Local]")"
    }

    /// "Progress: …" row of the status item menu.
    static func progressLine(status: MinerStatus) -> String {
        let bar = renderProgressBar(percent: status.activeDropPercent, length: 10)
        let timeInfo = status.activeDropMinutes ?? ""
        return "Progress: \(bar) \(Int(status.activeDropPercent))% (\(timeInfo) • \(status.activeDropRemaining)m left)"
    }

    static func renderProgressBar(percent: Double, length: Int = 10) -> String {
        let filled = max(0, min(length, Int(round(Double(length) * (percent / 100.0)))))
        let empty = length - filled
        return "[\(String(repeating: "█", count: filled))\(String(repeating: "░", count: empty))]"
    }

    static func formatMinutes(_ totalMinutes: Int) -> String {
        if totalMinutes >= 60 {
            let h = totalMinutes / 60
            let m = totalMinutes % 60
            return m > 0 ? "\(h)h \(m)m" : "\(h)h"
        }
        return "\(totalMinutes)m"
    }
}
