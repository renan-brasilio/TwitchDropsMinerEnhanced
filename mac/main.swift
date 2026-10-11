import AppKit
import WebKit
import Foundation

class WindowDelegate: NSObject, NSWindowDelegate {
    weak var appDelegate: AppDelegate?

    func windowShouldClose(_ sender: NSWindow) -> Bool {
        guard let appDelegate else { return true }
        if appDelegate.keepRunningOnClose {
            appDelegate.closeToMenuBar()
        } else {
            appDelegate.quitApp()
        }
        return false
    }
}

class AppDelegate: NSObject, NSApplicationDelegate, URLSessionWebSocketDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    var mainWindow: NSWindow!
    var webView: WKWebView!
    var statusItem: NSStatusItem!
    var windowDelegate = WindowDelegate()

    var webSocketTask: URLSessionWebSocketTask?
    var pollTimer: Timer?
    var backendProcess: Process?

    // Configuration
    var connection: ConnectionSettings {
        get {
            let defaults = UserDefaults.standard
            return ConnectionSettings(
                isRemote: defaults.bool(forKey: "isRemote"),
                serverURL: defaults.string(forKey: "serverURL"),
                apiKey: defaults.string(forKey: "apiKey")
            )
        }
        set {
            let defaults = UserDefaults.standard
            defaults.set(newValue.isRemote, forKey: "isRemote")
            defaults.set(newValue.serverURL, forKey: "serverURL")
            defaults.set(newValue.apiKey, forKey: "apiKey")
        }
    }
    var isRemote: Bool { connection.isRemote }
    var effectiveURL: URL { connection.effectiveURL }

    var menuBarStyle: MenuBarStyle {
        get {
            let raw = UserDefaults.standard.string(forKey: "menuBarStyle") ?? "iconOnly"
            return MenuBarStyle(rawValue: raw) ?? .iconOnly
        }
        set { UserDefaults.standard.set(newValue.rawValue, forKey: "menuBarStyle") }
    }

    var keepRunningOnClose: Bool {
        get { UserDefaults.standard.object(forKey: "keepRunningOnClose") as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: "keepRunningOnClose") }
    }

    // State cache
    var status = MinerStatus()
    var hasLoadedDashboard: Bool = false
    func applicationDidFinishLaunching(_ notification: Notification) {
        // Set Dock Application Icon
        if let iconPath = Bundle.main.path(forResource: "AppIcon", ofType: "icns"),
           let iconImg = NSImage(contentsOfFile: iconPath) {
            NSApp.applicationIconImage = iconImg
        }

        // 1. Setup Menu Bar Item
        setupStatusItem()

        // 2. Setup Main Native App Window
        setupMainWindow()

        // 3. Setup App Menus (for Cmd+Q, Cmd+W, Cmd+R shortcuts)
        setupMainMenu()

        // 4. In Local mode, verify and launch local backend if needed
        if !isRemote {
            checkOrStartLocalBackend()
        }

        // 5. Connect and Load Web View
        reloadConnection()

        // 6. Polling backup timer
        pollTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.pollStatus()
        }

        // Make app active and show window
        NSApp.activate(ignoringOtherApps: true)
        mainWindow.makeKeyAndOrderFront(nil)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMainWindow()
        return true
    }

    func applicationWillTerminate(_ notification: Notification) {
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        if let proc = backendProcess, proc.isRunning {
            proc.terminate()
        }
    }

    // MARK: - Native App Window & WebKit
    func setupMainWindow() {
        let screenRect = NSScreen.main?.visibleFrame ?? NSRect(x: 100, y: 100, width: 1100, height: 750)
        let width: CGFloat = 1120
        let height: CGFloat = 780
        let x = screenRect.midX - (width / 2)
        let y = screenRect.midY - (height / 2)
        let windowRect = NSRect(x: x, y: y, width: width, height: height)

        let style: NSWindow.StyleMask = [.titled, .closable, .miniaturizable, .resizable]
        mainWindow = NSWindow(contentRect: windowRect, styleMask: style, backing: .buffered, defer: false)
        mainWindow.title = "Twitch Drops Miner Enhanced"
        mainWindow.titlebarAppearsTransparent = false
        mainWindow.isReleasedWhenClosed = false
        mainWindow.minSize = NSSize(width: 820, height: 600)
        windowDelegate.appDelegate = self
        mainWindow.delegate = windowDelegate

        // WebKit Configuration
        let config = WKWebViewConfiguration()
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")
        config.websiteDataStore = WKWebsiteDataStore.nonPersistent()
        config.userContentController.add(self, name: "settings")

        webView = WKWebView(frame: mainWindow.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        webView.uiDelegate = self
        mainWindow.contentView?.addSubview(webView)
    }

    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        let alert = NSAlert()
        alert.messageText = "Twitch Drops Miner"
        alert.informativeText = message
        alert.alertStyle = .informational
        alert.runModal()
        completionHandler()
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        hasLoadedDashboard = true
        publishNativeSettings()
    }

    func publishNativeSettings(error: String? = nil) {
        let settings = NativeSettingsPayload.make(
            keepRunningOnClose: keepRunningOnClose,
            menuBarStyle: menuBarStyle,
            connection: connection,
            error: error
        )
        guard let script = NativeSettingsPayload.script(for: settings) else { return }
        webView.evaluateJavaScript(script, completionHandler: nil)
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard let parsed = BridgeMessage.parse(
            name: message.name,
            isMainFrame: message.frameInfo.isMainFrame,
            body: message.body,
            frameURL: message.frameInfo.request.url,
            connection: connection
        ) else { return }
        switch parsed {
        case .openConnectionSettings:
            // Recovery path from the offline page, which has no dashboard to configure.
            showConnectionSettings()
        case .connection(let request):
            applyConnection(request)
        case .preferences(let keepRunning, let style):
            if let keepRunning { keepRunningOnClose = keepRunning }
            if let style { menuBarStyle = style }
            setupMainMenu()
            updateUI()
            publishNativeSettings()
        }
    }

    func applyConnection(_ request: [String: Any]) {
        switch connection.applying(request) {
        case .failure(let error):
            publishNativeSettings(error: error.message)
        case .success(let updated):
            connection = updated
            if !updated.isRemote {
                checkOrStartLocalBackend()
            }
            reloadConnection()
            updateUI()
        }
    }

    @objc func showSettings() {
        showMainWindow()
        webView.evaluateJavaScript("switchTab('settings')", completionHandler: nil)
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        hasLoadedDashboard = false
        showRetryHTML()
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        hasLoadedDashboard = false
        showRetryHTML()
    }

    func showRetryHTML() {
        let modeStr = isRemote ? "Remote Server (\(effectiveURL.absoluteString))" : "Local Daemon (\(effectiveURL.absoluteString))"
        let errorHTML = """
        <!DOCTYPE html><html><head><meta charset="utf-8">
        <style>
        body { background: #0e0e10; color: #efeff1; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; text-align: center; }
        .card { background: #18181b; border: 1px solid #2d2d35; border-radius: 8px; padding: 32px 36px; max-width: 440px; }
        h2 { margin-bottom: 8px; font-size: 16px; font-weight: 600; }
        p { color: #adadb8; font-size: 13px; line-height: 1.5; margin-bottom: 20px; }
        .btn { background: #9146ff; color: #fff; border: 1px solid #9146ff; border-radius: 6px; padding: 8px 16px; font-weight: 500; font-size: 13px; cursor: pointer; }
        .btn:hover { background: #772ce8; }
        code { background: #1f1f23; padding: 2px 5px; border-radius: 4px; color: #efeff1; font-family: monospace; font-size: 12px; }
        </style></head><body>
        <div class="card">
        <h2>Connecting to Miner Daemon</h2>
        <p>Target: <code>\(modeStr)</code><br>The service is starting. This view will refresh automatically once connected.</p>
        <button class="btn" onclick="location.reload()">Retry Connection</button>
        <button class="btn" onclick="window.webkit.messageHandlers.settings.postMessage({openConnectionSettings: true})">Connection Settings…</button>
        </div>
        <script>setTimeout(function(){ location.reload(); }, 2000);</script>
        </body></html>
        """
        webView.loadHTMLString(errorHTML, baseURL: effectiveURL)
    }

    func showMainWindow() {
        NSApp.setActivationPolicy(.regular)
        mainWindow.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func setupMainMenu() {
        let mainMenu = NSMenu()

        // App Menu
        let appMenuItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(NSMenuItem(title: "About Twitch Drops Miner Enhanced", action: nil, keyEquivalent: ""))
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(NSMenuItem(title: "Settings...", action: #selector(showSettings), keyEquivalent: ","))
        appMenu.addItem(NSMenuItem(title: "Connection Settings...", action: #selector(showConnectionSettings), keyEquivalent: ""))
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(NSMenuItem(title: "Close Window", action: #selector(closeMainWindow), keyEquivalent: "w"))
        appMenu.addItem(NSMenuItem(title: "Close to Menu Bar", action: #selector(closeToMenuBar), keyEquivalent: ""))
        appMenu.addItem(makeCloseBehaviorItem())
        appMenu.addItem(NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q"))
        appMenuItem.submenu = appMenu
        mainMenu.addItem(appMenuItem)

        // View Menu
        let viewMenuItem = NSMenuItem()
        let viewMenu = NSMenu(title: "View")
        viewMenu.addItem(NSMenuItem(title: "Reload Dashboard", action: #selector(reloadWebView), keyEquivalent: "r"))
        viewMenu.addItem(NSMenuItem(title: "Show Main Window", action: #selector(showMainWindowAction), keyEquivalent: "1"))
        viewMenuItem.submenu = viewMenu
        mainMenu.addItem(viewMenuItem)

        NSApp.mainMenu = mainMenu
    }

    @objc func closeMainWindow() {
        mainWindow.performClose(nil)
    }

    @objc func closeToMenuBar() {
        mainWindow.orderOut(nil)
        NSApp.setActivationPolicy(.accessory)
    }

    func makeCloseBehaviorItem() -> NSMenuItem {
        let item = NSMenuItem(
            title: "Keep Running in Menu Bar When Window Closes",
            action: #selector(toggleKeepRunningOnClose),
            keyEquivalent: ""
        )
        item.target = self
        item.state = keepRunningOnClose ? .on : .off
        return item
    }

    @objc func toggleKeepRunningOnClose() {
        keepRunningOnClose.toggle()
        setupMainMenu()
        buildMenu()
        publishNativeSettings()
    }

    @objc func showMainWindowAction() {
        showMainWindow()
    }

    @objc func reloadWebView() {
        webView.reload()
        connectWebSocket()
    }

    // MARK: - Menu Bar Setup
    func setupStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            if let iconPath = Bundle.main.path(forResource: "MenuBarIcon", ofType: "png"),
               let iconImg = NSImage(contentsOfFile: iconPath) {
                iconImg.isTemplate = true
                button.image = iconImg
                button.imagePosition = .imageOnly
            } else if let iconImg = NSImage(contentsOfFile: "/Users/renanrocha/tmp/TwitchDropsMinerEnhanced/icons/MenuBarIcon.png") {
                iconImg.isTemplate = true
                button.image = iconImg
                button.imagePosition = .imageOnly
            } else {
                button.title = "⛏️"
            }
            button.toolTip = "Twitch Drops Miner Enhanced"
        }
        buildMenu()
    }

    func buildMenu() {
        let menu = NSMenu()

        // 1. Show Main Window Action
        let showItem = NSMenuItem(title: "Open Dashboard Window", action: #selector(showMainWindowAction), keyEquivalent: "o")
        showItem.attributedTitle = NSAttributedString(
            string: "Open Dashboard Window",
            attributes: [.font: NSFont.boldSystemFont(ofSize: 13)]
        )
        menu.addItem(showItem)
        menu.addItem(NSMenuItem(title: "Close to Menu Bar", action: #selector(closeToMenuBar), keyEquivalent: ""))

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
            menu.addItem(NSMenuItem(title: "Resume Mining", action: #selector(resumeMining), keyEquivalent: "r"))
        } else {
            menu.addItem(NSMenuItem(title: "Pause Mining", action: #selector(pauseMining), keyEquivalent: "p"))
        }
        menu.addItem(NSMenuItem(title: "Next Streamer", action: #selector(switchStreamer), keyEquivalent: "s"))
        menu.addItem(NSMenuItem(title: "Claim Eligible Drops", action: #selector(claimDropsAction), keyEquivalent: "c"))
        menu.addItem(NSMenuItem(title: "Reload Campaigns", action: #selector(reloadCampaigns), keyEquivalent: ""))

        // Activation Code Quick Copy (if device code is required)
        if let code = status.deviceCode {
            menu.addItem(NSMenuItem.separator())
            let copyItem = NSMenuItem(title: "Copy Activation Code: \(code)", action: #selector(copyActivationCodeAction), keyEquivalent: "")
            copyItem.attributedTitle = NSAttributedString(
                string: "Copy Code: \(code)",
                attributes: [.font: NSFont.boldSystemFont(ofSize: 12), .foregroundColor: NSColor.systemOrange]
            )
            menu.addItem(copyItem)
            menu.addItem(NSMenuItem(title: "Open Twitch Activation Page...", action: #selector(openActivationPage), keyEquivalent: ""))
        }

        menu.addItem(NSMenuItem.separator())
        // 6. Settings & Config
        menu.addItem(NSMenuItem(title: "Settings...", action: #selector(showSettings), keyEquivalent: ","))
        menu.addItem(NSMenuItem(title: "Connection Settings...", action: #selector(showConnectionSettings), keyEquivalent: ""))
        menu.addItem(makeCloseBehaviorItem())

        // 6b. Menu Bar Display Submenu
        let displaySubmenu = NSMenu(title: "Menu Bar Display")
        for style in MenuBarStyle.allCases {
            let item = NSMenuItem(title: style.title, action: #selector(changeMenuBarStyle(_:)), keyEquivalent: "")
            item.representedObject = style.rawValue
            item.state = (menuBarStyle == style) ? .on : .off
            displaySubmenu.addItem(item)
        }
        let displayMenuItem = NSMenuItem(title: "Menu Bar Display", action: nil, keyEquivalent: "")
        displayMenuItem.submenu = displaySubmenu
        menu.addItem(displayMenuItem)

        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q"))

        statusItem.menu = menu
    }

    func updateUI() {
        if let button = statusItem.button {
            button.title = StatusPresentation.menuBarTitle(style: menuBarStyle, status: status)
            button.imagePosition = button.title.isEmpty ? .imageOnly : .imageLeading
            button.toolTip = StatusPresentation.toolTip(status: status)
        }
        buildMenu()

        // Update main window title with status
        mainWindow.title = StatusPresentation.windowTitle(status: status, connection: connection)
    }

    @objc func changeMenuBarStyle(_ sender: NSMenuItem) {
        if let raw = sender.representedObject as? String,
           let style = MenuBarStyle(rawValue: raw) {
            self.menuBarStyle = style
            self.updateUI()
            self.publishNativeSettings()
        }
    }

    // MARK: - Local Backend Auto-Start
    func checkOrStartLocalBackend() {
        let url = URL(string: "http://127.0.0.1:23432/api/status")!
        var request = URLRequest(url: url)
        request.timeoutInterval = 1.0

        URLSession.shared.dataTask(with: request) { [weak self] _, response, error in
            if error != nil {
                DispatchQueue.main.async {
                    self?.spawnLocalBackend()
                }
            }
        }.resume()
    }

    func spawnLocalBackend() {
        let fileManager = FileManager.default
        let possibleDirs = [
            Bundle.main.bundleURL.deletingLastPathComponent().path,
            (Bundle.main.bundleURL.deletingLastPathComponent().path as NSString).appendingPathComponent(".."),
            "/Users/renanrocha/tmp/TwitchDropsMinerEnhanced",
            fileManager.currentDirectoryPath
        ]

        var scriptPath: String? = nil
        var venvPython: String? = nil

        for dir in possibleDirs {
            let candidateScript = (dir as NSString).appendingPathComponent("main.py")
            let candidateVenv = (dir as NSString).appendingPathComponent("venv/bin/python")
            if fileManager.fileExists(atPath: candidateScript) {
                scriptPath = candidateScript
                if fileManager.fileExists(atPath: candidateVenv) {
                    venvPython = candidateVenv
                }
                break
            }
        }

        guard let script = scriptPath else { return }
        let scriptDir = (script as NSString).deletingLastPathComponent
        let venvCandidates = [
            (scriptDir as NSString).appendingPathComponent("venv/bin/python"),
            "/Users/renanrocha/tmp/TwitchDropsMinerEnhanced/venv/bin/python",
            venvPython ?? ""
        ]
        var pythonExec = "/usr/bin/python3"
        for p in venvCandidates {
            if !p.isEmpty && fileManager.fileExists(atPath: p) {
                pythonExec = p
                break
            }
        }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", "exec \"\(pythonExec)\" \"\(script)\" --no-browser"]
        process.currentDirectoryURL = URL(fileURLWithPath: scriptDir)

        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        pipe.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            if let str = String(data: data, encoding: .utf8), !str.isEmpty {
                print("[Backend]: \(str.trimmingCharacters(in: .whitespacesAndNewlines))")
            }
        }

        do {
            try process.run()
            self.backendProcess = process
            print("Spawned local backend using \(pythonExec) (PID \(process.processIdentifier))")
        } catch {
            print("Failed to spawn local backend: \(error)")
        }
    }

    // MARK: - Connection & WebSockets
    func reloadConnection() {
        let connection = self.connection
        var request = URLRequest(url: connection.dashboardURL)
        request.cachePolicy = .reloadIgnoringLocalAndRemoteCacheData
        if let authorization = connection.authorizationHeader {
            request.setValue(authorization, forHTTPHeaderField: "Authorization")
        }
        webView.load(request)
        connectWebSocket()
    }

    func connectWebSocket() {
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        guard let wsURL = connection.webSocketURL else { return }
        let session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        webSocketTask = session.webSocketTask(with: wsURL)
        webSocketTask?.resume()
        receiveWebSocketMessage()
    }

    func receiveWebSocketMessage() {
        webSocketTask?.receive { [weak self] result in
            switch result {
            case .success(let message):
                switch message {
                case .string(let text):
                    self?.handleWebSocketText(text)
                case .data(let data):
                    if let text = String(data: data, encoding: .utf8) {
                        self?.handleWebSocketText(text)
                    }
                @unknown default:
                    break
                }
                self?.receiveWebSocketMessage()
            case .failure:
                DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) { [weak self] in
                    self?.connectWebSocket()
                }
            }
        }
    }

    func handleWebSocketText(_ text: String) {
        guard let message = MinerStatus.decodeWebSocketMessage(text) else { return }

        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            if self.status.apply(event: message.event, payload: message.payload) {
                self.updateUI()
            }
        }
    }

    func pollStatus() {
        let connection = self.connection
        guard let url = connection.apiURL("/api/status") else { return }
        var req = URLRequest(url: url)
        if let authorization = connection.authorizationHeader {
            req.setValue(authorization, forHTTPHeaderField: "Authorization")
        }
        URLSession.shared.dataTask(with: req) { [weak self] data, _, error in
            guard let data = data, error == nil,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                DispatchQueue.main.async {
                    guard let self, self.status.minerState != "OFFLINE" else { return }
                    self.status.minerState = "OFFLINE"
                    self.status.statusText = "Backend not reachable"
                    self.updateUI()
                }
                return
            }
            DispatchQueue.main.async {
                self?.parseStateJSON(json)
                if !(self?.hasLoadedDashboard ?? false) {
                    self?.hasLoadedDashboard = true
                    self?.reloadConnection()
                }
            }
        }.resume()
    }

    func parseStateJSON(_ json: [String: Any]) {
        status.apply(snapshot: json)
        updateUI()
    }

    // MARK: - Actions
    func sendPostRequest(endpoint: String, json: [String: Any]? = nil) {
        let connection = self.connection
        guard let url = connection.apiURL(endpoint) else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if let authorization = connection.authorizationHeader {
            request.setValue(authorization, forHTTPHeaderField: "Authorization")
        }

        if let json = json, let data = try? JSONSerialization.data(withJSONObject: json) {
            request.httpBody = data
        }

        URLSession.shared.dataTask(with: request) { [weak self] _, _, _ in
            self?.pollStatus()
        }.resume()
    }

    @objc func pauseMining() { sendPostRequest(endpoint: "/api/control/pause") }
    @objc func resumeMining() { sendPostRequest(endpoint: "/api/control/resume") }
    @objc func switchStreamer() { sendPostRequest(endpoint: "/api/control/switch") }
    @objc func reloadCampaigns() { sendPostRequest(endpoint: "/api/control/reload") }
    @objc func claimDropsAction() { sendPostRequest(endpoint: "/api/control/claim") }

    @objc func copyActivationCodeAction() {
        if let code = status.deviceCode {
            let pb = NSPasteboard.general
            pb.clearContents()
            pb.setString(code, forType: .string)
        }
    }

    @objc func openActivationPage() {
        let uri = status.deviceUri ?? "https://www.twitch.tv/activate"
        if let url = URL(string: uri) {
            NSWorkspace.shared.open(url)
        }
    }


    // MARK: - Connection Settings Modal (Local vs Remote VPS)
    @objc func showConnectionSettings() {
        let alert = NSAlert()
        alert.messageText = "Connection Settings"
        alert.informativeText = "Configure whether this app connects to a local miner or a remote VPS Docker container:"
        alert.alertStyle = .informational

        let container = NSView(frame: NSRect(x: 0, y: 0, width: 420, height: 210))

        // Mode Segmented Control
        let modeLabel = NSTextField(labelWithString: "Connection Mode:")
        modeLabel.frame = NSRect(x: 0, y: 175, width: 140, height: 20)
        modeLabel.font = NSFont.boldSystemFont(ofSize: 12)
        container.addSubview(modeLabel)

        let modeSegment = NSSegmentedControl(labels: ["Local Daemon", "Remote VPS / Docker"], trackingMode: .selectOne, target: nil, action: nil)
        modeSegment.frame = NSRect(x: 140, y: 173, width: 270, height: 24)
        modeSegment.selectedSegment = isRemote ? 1 : 0
        container.addSubview(modeSegment)

        // Remote URL
        let urlLabel = NSTextField(labelWithString: "Server URL:")
        urlLabel.frame = NSRect(x: 0, y: 135, width: 140, height: 20)
        container.addSubview(urlLabel)

        let urlInput = NSTextField(frame: NSRect(x: 140, y: 132, width: 270, height: 24))
        urlInput.stringValue = connection.serverURL
        urlInput.placeholderString = "http://100.77.235.18:23432 or https://..."
        container.addSubview(urlInput)

        // API Key
        let keyLabel = NSTextField(labelWithString: "API Key (Optional):")
        keyLabel.frame = NSRect(x: 0, y: 95, width: 140, height: 20)
        container.addSubview(keyLabel)

        let keyInput = NSSecureTextField(frame: NSRect(x: 140, y: 92, width: 270, height: 24))
        keyInput.stringValue = connection.apiKey
        keyInput.placeholderString = "Leave empty if not required"
        container.addSubview(keyInput)

        // Menu Bar Display Picker
        let displayLabel = NSTextField(labelWithString: "Menu Bar Display:")
        displayLabel.frame = NSRect(x: 0, y: 55, width: 140, height: 20)
        container.addSubview(displayLabel)

        let displayPopup = NSPopUpButton(frame: NSRect(x: 140, y: 52, width: 270, height: 26), pullsDown: false)
        for style in MenuBarStyle.allCases {
            displayPopup.addItem(withTitle: style.title)
        }
        displayPopup.selectItem(withTitle: menuBarStyle.title)
        container.addSubview(displayPopup)

        let hint = NSTextField(labelWithString: "Local uses http://127.0.0.1:23432. Remote connects to your Docker container.")
        hint.frame = NSRect(x: 0, y: 8, width: 420, height: 32)
        hint.font = NSFont.systemFont(ofSize: 11)
        hint.textColor = NSColor.secondaryLabelColor
        container.addSubview(hint)

        alert.accessoryView = container
        alert.addButton(withTitle: "Save & Connect")
        alert.addButton(withTitle: "Cancel")

        if alert.runModal() == .alertFirstButtonReturn {
            connection = ConnectionSettings.fromConnectionDialog(
                remote: modeSegment.selectedSegment == 1,
                serverURL: urlInput.stringValue,
                apiKey: keyInput.stringValue
            )

            if let selectedItem = displayPopup.selectedItem,
               let chosenStyle = MenuBarStyle.allCases.first(where: { $0.title == selectedItem.title }) {
                self.menuBarStyle = chosenStyle
            }

            print("Updated settings: isRemote=\(isRemote), url=\(connection.serverURL), style=\(menuBarStyle)")
            reloadConnection()
            updateUI()
        }
    }

    @objc func quitApp() {
        NSApplication.shared.terminate(nil)
    }
}

// App Entry Point
let app = NSApplication.shared
app.setActivationPolicy(.regular) // Full native macOS application with Dock icon and Window!
let delegate = AppDelegate()
app.delegate = delegate
app.run()
