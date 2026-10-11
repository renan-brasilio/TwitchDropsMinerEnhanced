import AppKit

@MainActor
enum MenuActions {
    static let showMainWindow = Selector(("showMainWindowAction"))
    static let closeToMenuBar = Selector(("closeToMenuBar"))
    static let resumeMining = Selector(("resumeMining"))
    static let pauseMining = Selector(("pauseMining"))
    static let switchStreamer = Selector(("switchStreamer"))
    static let claimDrops = Selector(("claimDropsAction"))
    static let reloadCampaigns = Selector(("reloadCampaigns"))
    static let copyActivationCode = Selector(("copyActivationCodeAction"))
    static let openActivationPage = Selector(("openActivationPage"))
    static let showSettings = Selector(("showSettings"))
    static let showConnectionSettings = Selector(("showConnectionSettings"))
    static let toggleKeepRunning = Selector(("toggleKeepRunningOnClose"))
    static let changeMenuBarStyle = Selector(("changeMenuBarStyle:"))
    static let quit = Selector(("quitApp"))
}

@MainActor
enum StatusMenuBuilder {
    static func buildMenu(
        status: MinerStatus,
        isRemote: Bool,
        menuBarStyle: MenuBarStyle,
        keepRunningOnClose: Bool,
        target: AnyObject? = nil
    ) -> NSMenu {
        let menu = NSMenu()

        // 1. Show Main Window Action
        let showItem = NSMenuItem(title: "Open Dashboard Window", action: MenuActions.showMainWindow, keyEquivalent: "o")
        showItem.attributedTitle = NSAttributedString(
            string: "Open Dashboard Window",
            attributes: [.font: NSFont.boldSystemFont(ofSize: 13)]
        )
        showItem.target = target
        menu.addItem(showItem)

        let closeItem = NSMenuItem(title: "Close to Menu Bar", action: MenuActions.closeToMenuBar, keyEquivalent: "")
        closeItem.target = target
        menu.addItem(closeItem)

        menu.addItem(NSMenuItem.separator())

        // 2. Status Row with Indicator
        menu.addItem(NSMenuItem(title: StatusPresentation.statusLine(status: status, isRemote: isRemote), action: nil, keyEquivalent: ""))

        let detailItem = NSMenuItem(title: "Task: \(status.statusText)", action: nil, keyEquivalent: "")
        menu.addItem(detailItem)

        menu.addItem(NSMenuItem.separator())

        // 3. Active Drop & Game
        if let drop = status.activeDropName, let game = status.activeGame {
            let dropHeader = NSMenuItem(title: "Game: \(game)", action: nil, keyEquivalent: "")
            dropHeader.attributedTitle = NSAttributedString(
                string: "Game: \(game)",
                attributes: [.font: NSFont.systemFont(ofSize: 12, weight: .semibold)]
            )
            menu.addItem(dropHeader)

            let dropItem = NSMenuItem(title: "Reward: \(drop)", action: nil, keyEquivalent: "")
            menu.addItem(dropItem)

            let progressStr = StatusPresentation.progressLine(status: status)
            let progressItem = NSMenuItem(title: progressStr, action: nil, keyEquivalent: "")
            menu.addItem(progressItem)
        } else {
            let noDropItem = NSMenuItem(title: "Reward: None active", action: nil, keyEquivalent: "")
            noDropItem.isEnabled = false
            menu.addItem(noDropItem)
        }

        // 4. Streamer info
        if let ch = status.activeChannel {
            let streamerStr = "Channel: \(ch) (\(status.activeViewers.formatted()) viewers)"
            menu.addItem(NSMenuItem(title: streamerStr, action: nil, keyEquivalent: ""))
        } else {
            let noStreamer = NSMenuItem(title: "Channel: None", action: nil, keyEquivalent: "")
            noStreamer.isEnabled = false
            menu.addItem(noStreamer)
        }

        menu.addItem(NSMenuItem.separator())

        // 5. Mining Controls
        if status.minerState == "IDLE" {
            let resumeItem = NSMenuItem(title: "Resume Mining", action: MenuActions.resumeMining, keyEquivalent: "r")
            resumeItem.target = target
            menu.addItem(resumeItem)
        } else {
            let pauseItem = NSMenuItem(title: "Pause Mining", action: MenuActions.pauseMining, keyEquivalent: "p")
            pauseItem.target = target
            menu.addItem(pauseItem)
        }
        let switchItem = NSMenuItem(title: "Next Streamer", action: MenuActions.switchStreamer, keyEquivalent: "s")
        switchItem.target = target
        menu.addItem(switchItem)

        let claimItem = NSMenuItem(title: "Claim Eligible Drops", action: MenuActions.claimDrops, keyEquivalent: "c")
        claimItem.target = target
        menu.addItem(claimItem)

        let reloadItem = NSMenuItem(title: "Reload Campaigns", action: MenuActions.reloadCampaigns, keyEquivalent: "")
        reloadItem.target = target
        menu.addItem(reloadItem)

        // Activation Code Quick Copy (if device code is required)
        if let code = status.deviceCode {
            menu.addItem(NSMenuItem.separator())
            let copyItem = NSMenuItem(title: "Copy Activation Code: \(code)", action: MenuActions.copyActivationCode, keyEquivalent: "")
            copyItem.attributedTitle = NSAttributedString(
                string: "Copy Code: \(code)",
                attributes: [.font: NSFont.boldSystemFont(ofSize: 12), .foregroundColor: NSColor.systemOrange]
            )
            copyItem.target = target
            menu.addItem(copyItem)

            let openActItem = NSMenuItem(title: "Open Twitch Activation Page...", action: MenuActions.openActivationPage, keyEquivalent: "")
            openActItem.target = target
            menu.addItem(openActItem)
        }

        menu.addItem(NSMenuItem.separator())
        // 6. Settings & Config
        let settingsItem = NSMenuItem(title: "Settings...", action: MenuActions.showSettings, keyEquivalent: ",")
        settingsItem.target = target
        menu.addItem(settingsItem)

        let connItem = NSMenuItem(title: "Connection Settings...", action: MenuActions.showConnectionSettings, keyEquivalent: "")
        connItem.target = target
        menu.addItem(connItem)

        let keepRunningItem = NSMenuItem(
            title: "Keep Running in Menu Bar When Window Closes",
            action: MenuActions.toggleKeepRunning,
            keyEquivalent: ""
        )
        keepRunningItem.state = keepRunningOnClose ? .on : .off
        keepRunningItem.target = target
        menu.addItem(keepRunningItem)

        // 6b. Menu Bar Display Submenu
        let displaySubmenu = NSMenu(title: "Menu Bar Display")
        for style in MenuBarStyle.allCases {
            let item = NSMenuItem(title: style.title, action: MenuActions.changeMenuBarStyle, keyEquivalent: "")
            item.representedObject = style.rawValue
            item.state = (menuBarStyle == style) ? .on : .off
            item.target = target
            displaySubmenu.addItem(item)
        }
        let displayMenuItem = NSMenuItem(title: "Menu Bar Display", action: nil, keyEquivalent: "")
        displayMenuItem.submenu = displaySubmenu
        menu.addItem(displayMenuItem)

        menu.addItem(NSMenuItem.separator())
        let quitItem = NSMenuItem(title: "Quit", action: MenuActions.quit, keyEquivalent: "q")
        quitItem.target = target
        menu.addItem(quitItem)

        return menu
    }
}
