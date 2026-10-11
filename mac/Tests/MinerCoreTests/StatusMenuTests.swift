import AppKit
import Testing
@testable import MinerCore

@Suite("Status item menu builder")
struct StatusMenuTests {
    private func makeStatus(
        state: String = "WATCHING",
        game: String? = "Overwatch",
        drop: String? = "Epic Chest",
        percent: Double = 75.0,
        remaining: Int = 30,
        channel: String? = "streamer1",
        viewers: Int = 1234,
        deviceCode: String? = nil
    ) -> MinerStatus {
        var status = MinerStatus()
        status.minerState = state
        status.statusText = "Watching: \(channel ?? "None")"
        status.activeGame = game
        status.activeDropName = drop
        status.activeDropPercent = percent
        status.activeDropRemaining = remaining
        status.activeDropMinutes = "45/60m"
        status.activeChannel = channel
        status.activeViewers = viewers
        status.deviceCode = deviceCode
        return status
    }

    @Test("Menu item actions and key equivalents")
    @MainActor
    func menuActionsAndShortcuts() {
        let status = makeStatus()
        let menu = StatusMenuBuilder.buildMenu(
            status: status,
            isRemote: false,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: true
        )

        let titles = menu.items.map { $0.title }
        #expect(titles.contains("Open Dashboard Window"))
        #expect(titles.contains("Close to Menu Bar"))
        #expect(titles.contains("Next Streamer"))
        #expect(titles.contains("Claim Eligible Drops"))
        #expect(titles.contains("Settings..."))
        #expect(titles.contains("Connection Settings..."))
        #expect(titles.contains("Quit"))

        let openItem = menu.items.first { $0.title == "Open Dashboard Window" }
        #expect(openItem?.keyEquivalent == "o")
        #expect(openItem?.action == MenuActions.showMainWindow)

        let settingsItem = menu.items.first { $0.title == "Settings..." }
        #expect(settingsItem?.keyEquivalent == ",")
        #expect(settingsItem?.action == MenuActions.showSettings)

        let quitItem = menu.items.first { $0.title == "Quit" }
        #expect(quitItem?.keyEquivalent == "q")
        #expect(quitItem?.action == MenuActions.quit)

        let nextItem = menu.items.first { $0.title == "Next Streamer" }
        #expect(nextItem?.keyEquivalent == "s")
        #expect(nextItem?.action == MenuActions.switchStreamer)

        let claimItem = menu.items.first { $0.title == "Claim Eligible Drops" }
        #expect(claimItem?.keyEquivalent == "c")
        #expect(claimItem?.action == MenuActions.claimDrops)
    }

    @Test("Mining controls toggle: Pause while active, Resume while idle")
    @MainActor
    func miningControlsToggle() {
        // While active -> Pause Mining (p)
        let activeStatus = makeStatus(state: "WATCHING")
        let activeMenu = StatusMenuBuilder.buildMenu(
            status: activeStatus,
            isRemote: false,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: true
        )
        let pauseItem = activeMenu.items.first { $0.title == "Pause Mining" }
        #expect(pauseItem != nil)
        #expect(pauseItem?.keyEquivalent == "p")
        #expect(pauseItem?.action == MenuActions.pauseMining)
        #expect(!activeMenu.items.contains { $0.title == "Resume Mining" })

        // While idle -> Resume Mining (r)
        let idleStatus = makeStatus(state: "IDLE")
        let idleMenu = StatusMenuBuilder.buildMenu(
            status: idleStatus,
            isRemote: false,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: true
        )
        let resumeItem = idleMenu.items.first { $0.title == "Resume Mining" }
        #expect(resumeItem != nil)
        #expect(resumeItem?.keyEquivalent == "r")
        #expect(resumeItem?.action == MenuActions.resumeMining)
        #expect(!idleMenu.items.contains { $0.title == "Pause Mining" })
    }

    @Test("Device activation items appear when device code is set, absent when nil")
    @MainActor
    func deviceActivationItems() {
        // With device code -> Copy Code and Open Activation items present
        let codeStatus = makeStatus(deviceCode: "ABC-123")
        let codeMenu = StatusMenuBuilder.buildMenu(
            status: codeStatus,
            isRemote: false,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: true
        )
        let copyItem = codeMenu.items.first { $0.title.contains("ABC-123") }
        #expect(copyItem != nil)
        #expect(copyItem?.action == MenuActions.copyActivationCode)

        let openActItem = codeMenu.items.first { $0.title.contains("Open Twitch Activation Page") }
        #expect(openActItem != nil)
        #expect(openActItem?.action == MenuActions.openActivationPage)

        // Without device code -> Neither item present
        let noCodeStatus = makeStatus(deviceCode: nil)
        let noCodeMenu = StatusMenuBuilder.buildMenu(
            status: noCodeStatus,
            isRemote: false,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: true
        )
        #expect(!noCodeMenu.items.contains { $0.title.contains("Copy Activation Code") })
        #expect(!noCodeMenu.items.contains { $0.title.contains("Open Twitch Activation Page") })
    }

    @Test("Active drop items vs none active placeholder")
    @MainActor
    func dropItemsPresence() {
        // With active drop
        let dropStatus = makeStatus(game: "Overwatch", drop: "Golden Box", percent: 75.0, remaining: 15)
        let dropMenu = StatusMenuBuilder.buildMenu(
            status: dropStatus,
            isRemote: false,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: true
        )
        #expect(dropMenu.items.contains { $0.title == "Game: Overwatch" })
        #expect(dropMenu.items.contains { $0.title == "Reward: Golden Box" })
        #expect(dropMenu.items.contains { $0.title.contains("75%") && $0.title.contains("15m left") })
        #expect(!dropMenu.items.contains { $0.title == "Reward: None active" })

        // Without active drop
        let noDropStatus = makeStatus(game: nil, drop: nil)
        let noDropMenu = StatusMenuBuilder.buildMenu(
            status: noDropStatus,
            isRemote: false,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: true
        )
        let noneItem = noDropMenu.items.first { $0.title == "Reward: None active" }
        #expect(noneItem != nil)
        #expect(noneItem?.isEnabled == false)
        #expect(!noDropMenu.items.contains { $0.title.starts(with: "Game:") })
    }

    @Test("Active channel item vs none placeholder")
    @MainActor
    func channelItemsPresence() {
        // With channel
        let channelStatus = makeStatus(channel: "cool_streamer", viewers: 5432)
        let channelMenu = StatusMenuBuilder.buildMenu(
            status: channelStatus,
            isRemote: false,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: true
        )
        #expect(channelMenu.items.contains { $0.title.contains("cool_streamer") && $0.title.contains("5,432") })

        // Without channel
        let noChannelStatus = makeStatus(channel: nil)
        let noChannelMenu = StatusMenuBuilder.buildMenu(
            status: noChannelStatus,
            isRemote: false,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: true
        )
        let noneItem = noChannelMenu.items.first { $0.title == "Channel: None" }
        #expect(noneItem != nil)
        #expect(noneItem?.isEnabled == false)
    }

    @Test("Menu Bar Display submenu contains all styles and checks the selected one")
    @MainActor
    func menuBarDisplaySubmenu() {
        for selectedStyle in MenuBarStyle.allCases {
            let menu = StatusMenuBuilder.buildMenu(
                status: makeStatus(),
                isRemote: false,
                menuBarStyle: selectedStyle,
                keepRunningOnClose: true
            )
            let displayItem = menu.items.first { $0.title == "Menu Bar Display" }
            #expect(displayItem != nil)
            guard let submenu = displayItem?.submenu else {
                Issue.record("Missing submenu on Menu Bar Display item")
                return
            }

            #expect(submenu.items.count == MenuBarStyle.allCases.count)
            for style in MenuBarStyle.allCases {
                let item = submenu.items.first { $0.title == style.title }
                #expect(item != nil)
                #expect(item?.action == MenuActions.changeMenuBarStyle)
                #expect(item?.representedObject as? String == style.rawValue)
                if style == selectedStyle {
                    #expect(item?.state == .on)
                } else {
                    #expect(item?.state == .off)
                }
            }
        }
    }

    @Test("Keep running menu item reflects boolean state")
    @MainActor
    func keepRunningCheckboxState() {
        let menuOn = StatusMenuBuilder.buildMenu(
            status: makeStatus(),
            isRemote: false,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: true
        )
        let itemOn = menuOn.items.first { $0.title == "Keep Running in Menu Bar When Window Closes" }
        #expect(itemOn?.state == .on)
        #expect(itemOn?.action == MenuActions.toggleKeepRunning)

        let menuOff = StatusMenuBuilder.buildMenu(
            status: makeStatus(),
            isRemote: false,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: false
        )
        let itemOff = menuOff.items.first { $0.title == "Keep Running in Menu Bar When Window Closes" }
        #expect(itemOff?.state == .off)
    }

    @Test("Status row indicates local vs remote mode")
    @MainActor
    func statusLineLocalVsRemote() {
        let localMenu = StatusMenuBuilder.buildMenu(
            status: makeStatus(state: "WATCHING"),
            isRemote: false,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: true
        )
        #expect(localMenu.items.contains { $0.title.contains("[Local]") })

        let remoteMenu = StatusMenuBuilder.buildMenu(
            status: makeStatus(state: "WATCHING"),
            isRemote: true,
            menuBarStyle: .iconOnly,
            keepRunningOnClose: true
        )
        #expect(remoteMenu.items.contains { $0.title.contains("[Remote]") })
    }
}
