import Foundation
import Testing
@testable import MinerCore

private func mining(percent: Double = 65.4, remaining: Int = 95, game: String? = "Overwatch", state: String = "WATCHING") -> MinerStatus {
    var status = MinerStatus()
    status.minerState = state
    status.statusText = "Watching: streamer"
    status.activeGame = game
    status.activeDropName = game == nil ? nil : "Reward"
    status.activeDropPercent = percent
    status.activeDropRemaining = remaining
    return status
}

private func idle() -> MinerStatus {
    var status = MinerStatus()
    status.minerState = "IDLE"
    status.statusText = "Paused by user"
    return status
}

@Suite("Menu bar title")
struct MenuBarTitleTests {
    @Test("Each style while mining", arguments: [
        (MenuBarStyle.iconOnly, ""),
        (.currentMining, " Overwatch"),
        (.timeLeft, " 1h 35m"),
        (.percentage, " 65%"),
        (.full, " 65% Overwatch"),
    ])
    func whileMining(style: MenuBarStyle, title: String) {
        #expect(StatusPresentation.menuBarTitle(style: style, status: mining()) == title)
    }

    @Test("Each style while paused with nothing active", arguments: [
        (MenuBarStyle.iconOnly, ""),
        (.currentMining, " Idle"),
        (.timeLeft, ""),
        (.percentage, ""),
        (.full, " Idle"),
    ])
    func whileIdle(style: MenuBarStyle, title: String) {
        #expect(StatusPresentation.menuBarTitle(style: style, status: idle()) == title)
    }

    @Test("Busy without a drop shows only the icon", arguments: MenuBarStyle.allCases)
    func busyWithoutDrop(style: MenuBarStyle) {
        let status = mining(percent: 0, remaining: 0, game: nil, state: "CHANNEL_SWITCH")
        #expect(StatusPresentation.menuBarTitle(style: style, status: status) == "")
    }

    @Test("Full status needs progress; a game at 0% shows only the icon")
    func fullNeedsProgress() {
        #expect(StatusPresentation.menuBarTitle(style: .full, status: mining(percent: 0)) == "")
        #expect(StatusPresentation.menuBarTitle(style: .currentMining, status: mining(percent: 0)) == " Overwatch")
    }

    @Test("Tooltip shows drop progress while mining and the status text otherwise")
    func toolTip() {
        #expect(StatusPresentation.toolTip(status: mining()) == "Twitch Drops Miner Enhanced — Overwatch: 65% (1h 35m remaining)")
        #expect(StatusPresentation.toolTip(status: idle()) == "Twitch Drops Miner Enhanced — Paused by user")
    }
}

@Suite("Status menu and window text")
struct StatusTextTests {
    @Test("Status row summarises the miner state and connection", arguments: [
        ("IDLE", false, "Status: ○ Idle [Local]"),
        ("OFFLINE", true, "Status: ■ Offline [Remote]"),
        ("CHANNEL_SWITCH", false, "Status: ↻ Syncing [Local]"),
        ("CHANNELS_FETCH", false, "Status: ↻ Syncing [Local]"),
        ("INVENTORY_FETCH", false, "Status: ↻ Syncing [Local]"),
        ("WATCHING", true, "Status: ● Active [Remote]"),
    ])
    func statusLine(state: String, remote: Bool, line: String) {
        #expect(StatusPresentation.statusLine(status: mining(state: state), isRemote: remote) == line)
    }

    @Test("Progress row shows the bar, percentage, minutes, and time left")
    func progressLine() {
        var status = mining(percent: 33.3, remaining: 40)
        status.activeDropMinutes = "20/60m"
        #expect(StatusPresentation.progressLine(status: status) == "Progress: [███░░░░░░░] 33% (20/60m • 40m left)")
    }

    @Test("Window title shows the status and where the miner runs")
    func windowTitle() {
        let remote = ConnectionSettings(isRemote: true, serverURL: "http://127.0.0.1:50123", apiKey: nil)
        #expect(StatusPresentation.windowTitle(status: mining(), connection: remote)
                == "Twitch Drops Miner Enhanced — Watching: streamer (Remote [127.0.0.1])")
        let local = ConnectionSettings(isRemote: false, serverURL: nil, apiKey: nil)
        #expect(StatusPresentation.windowTitle(status: idle(), connection: local) == "Twitch Drops Miner Enhanced — Paused by user (Local)")
    }
}

@Suite("Progress bar")
struct ProgressBarTests {
    @Test("Fills in proportion to the percentage, clamped to the bar", arguments: [
        (-20.0, "[░░░░░░░░░░]"),
        (0.0, "[░░░░░░░░░░]"),
        (4.9, "[░░░░░░░░░░]"),
        (5.0, "[█░░░░░░░░░]"),
        (33.3, "[███░░░░░░░]"),
        (99.0, "[██████████]"),
        (100.0, "[██████████]"),
        (250.0, "[██████████]"),
    ])
    func fill(percent: Double, bar: String) {
        #expect(StatusPresentation.renderProgressBar(percent: percent) == bar)
    }

    @Test("Honours a custom length")
    func customLength() {
        #expect(StatusPresentation.renderProgressBar(percent: 50, length: 4) == "[██░░]")
    }
}

@Suite("Minute formatting")
struct MinuteFormattingTests {
    @Test("Minutes below an hour, whole hours, and hours with minutes", arguments: [
        (0, "0m"), (1, "1m"), (59, "59m"), (60, "1h"), (61, "1h 1m"), (119, "1h 59m"), (120, "2h"), (1500, "25h"),
    ])
    func format(minutes: Int, text: String) {
        #expect(StatusPresentation.formatMinutes(minutes) == text)
    }
}
