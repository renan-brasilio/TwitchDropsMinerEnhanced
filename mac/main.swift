import AppKit
import WebKit
import Foundation

class WindowDelegate: NSObject, NSWindowDelegate {
    func windowShouldClose(_ sender: NSWindow) -> Bool {
        sender.orderOut(nil)
        return false // Hide instead of closing so menu bar stays active
    }
}

class AppDelegate: NSObject, NSApplicationDelegate, URLSessionWebSocketDelegate, WKNavigationDelegate {
    var mainWindow: NSWindow!
    var webView: WKWebView!
    var statusItem: NSStatusItem!
    var windowDelegate = WindowDelegate()

    var webSocketTask: URLSessionWebSocketTask?
    var pollTimer: Timer?
    var backendProcess: Process?

    // Configuration
    var isRemote: Bool {
        get { UserDefaults.standard.bool(forKey: "isRemote") }
        set { UserDefaults.standard.set(newValue, forKey: "isRemote") }
    }
    var serverURLString: String {
        get {
            let val = UserDefaults.standard.string(forKey: "serverURL") ?? ""
            return val.isEmpty ? "http://127.0.0.1:23432" : val
        }
        set { UserDefaults.standard.set(newValue, forKey: "serverURL") }
    }
    var apiKey: String {
        get { UserDefaults.standard.string(forKey: "apiKey") ?? "" }
        set { UserDefaults.standard.set(newValue, forKey: "apiKey") }
    }

    var effectiveURL: URL {
        let trimmed = serverURLString.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.hasPrefix("http://") || trimmed.hasPrefix("https://") {
            return URL(string: trimmed) ?? URL(string: "http://127.0.0.1:23432")!
        }
        return URL(string: "http://\(trimmed)") ?? URL(string: "http://127.0.0.1:23432")!
    }

    // State cache
    var minerState: String = "CONNECTING"
    var statusText: String = "Connecting..."
    var activeGame: String? = nil
    var activeDropName: String? = nil
    var activeDropPercent: Double = 0.0
    var activeDropMinutes: String? = nil
    var activeDropRemaining: Int = 0
    var activeChannel: String? = nil
    var activeViewers: Int = 0
    var isLoggedIn: Bool = false
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
        mainWindow.delegate = windowDelegate

        // WebKit Configuration
        let config = WKWebViewConfiguration()
        config.preferences.setValue(true, forKey: "developerExtrasEnabled")

        webView = WKWebView(frame: mainWindow.contentView!.bounds, configuration: config)
        webView.autoresizingMask = [.width, .height]
        webView.navigationDelegate = self
        mainWindow.contentView?.addSubview(webView)
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        hasLoadedDashboard = true
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
        body { background: #090a0f; color: #f0f3fa; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; display: flex; flex-direction: column; align-items: center; justify-content: center; height: 100vh; margin: 0; text-align: center; }
        .card { background: #12151e; border: 1px solid #1e2433; border-radius: 14px; padding: 36px 40px; max-width: 480px; box-shadow: 0 10px 30px rgba(0,0,0,0.5); }
        .icon { font-size: 40px; margin-bottom: 16px; }
        h2 { margin-bottom: 12px; font-size: 1.35rem; }
        p { color: #8c97b0; font-size: 0.9rem; line-height: 1.6; margin-bottom: 22px; }
        .btn { background: #9146ff; color: #fff; border: none; border-radius: 8px; padding: 10px 22px; font-weight: 600; font-size: 0.9rem; cursor: pointer; }
        .btn:hover { background: #772ce8; }
        code { background: rgba(255,255,255,0.08); padding: 2px 6px; border-radius: 4px; color: #f59e0b; }
        </style></head><body>
        <div class="card">
        <div class="icon">⛏️</div>
        <h2>Connecting to Miner Backend...</h2>
        <p>Connecting to <strong>\(modeStr)</strong>.<br>The daemon is initializing. This page will automatically refresh once the connection is established.</p>
        <button class="btn" onclick="location.reload()">Retry Connection</button>
        </div>
        <script>setTimeout(function(){ location.reload(); }, 2000);</script>
        </body></html>
        """
        webView.loadHTMLString(errorHTML, baseURL: effectiveURL)
    }

    func showMainWindow() {
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
        appMenu.addItem(NSMenuItem(title: "Connection Settings...", action: #selector(showConnectionSettings), keyEquivalent: ","))
        appMenu.addItem(NSMenuItem.separator())
        appMenu.addItem(NSMenuItem(title: "Hide Window", action: #selector(hideMainWindow), keyEquivalent: "w"))
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

    @objc func hideMainWindow() {
        mainWindow.orderOut(nil)
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
            button.title = "⛏️ Drops"
            button.toolTip = "Twitch Drops Miner Enhanced"
        }
        buildMenu()
    }

    func buildMenu() {
        let menu = NSMenu()

        // 1. Show Main Window Action
        let showItem = NSMenuItem(title: "🖥️ Show Application Window", action: #selector(showMainWindowAction), keyEquivalent: "o")
        showItem.attributedTitle = NSAttributedString(
            string: "🖥️ Show Application Window",
            attributes: [.font: NSFont.boldSystemFont(ofSize: 13)]
        )
        menu.addItem(showItem)

        menu.addItem(NSMenuItem.separator())

        // 2. Status Row with Indicator
        let statusDot: String
        switch minerState {
        case "IDLE": statusDot = "🟡 Idle"
        case "OFFLINE": statusDot = "🔴 Backend Offline"
        case "CHANNEL_SWITCH", "CHANNELS_FETCH", "INVENTORY_FETCH": statusDot = "🔄 Updating"
        default: statusDot = "🟢 Mining Active"
        }
        let modeStr = isRemote ? "(Remote VPS)" : "(Local)"
        let statusMenuItem = NSMenuItem(title: "Status: \(statusDot) \(modeStr)", action: nil, keyEquivalent: "")
        menu.addItem(statusMenuItem)

        let detailItem = NSMenuItem(title: "Activity: \(statusText)", action: nil, keyEquivalent: "")
        menu.addItem(detailItem)

        menu.addItem(NSMenuItem.separator())

        // 3. Active Drop & Game
        if let drop = activeDropName, let game = activeGame {
            let dropHeader = NSMenuItem(title: "🎮 Game: \(game)", action: nil, keyEquivalent: "")
            dropHeader.attributedTitle = NSAttributedString(
                string: "🎮 Game: \(game)",
                attributes: [.font: NSFont.systemFont(ofSize: 12, weight: .semibold)]
            )
            menu.addItem(dropHeader)

            let dropItem = NSMenuItem(title: "🎁 Drop: \(drop)", action: nil, keyEquivalent: "")
            menu.addItem(dropItem)

            let bar = renderProgressBar(percent: activeDropPercent, length: 12)
            let timeInfo = activeDropMinutes ?? ""
            let progressStr = "⏳ Progress: \(bar) \(Int(activeDropPercent))% (\(timeInfo) • \(activeDropRemaining)m left)"
            let progressItem = NSMenuItem(title: progressStr, action: nil, keyEquivalent: "")
            menu.addItem(progressItem)
        } else {
            let noDropItem = NSMenuItem(title: "🎁 Drop: No Active Drop", action: nil, keyEquivalent: "")
            noDropItem.isEnabled = false
            menu.addItem(noDropItem)
        }

        // 4. Streamer info
        if let ch = activeChannel {
            let streamerStr = "📺 Watching: \(ch) (\(activeViewers.formatted()) viewers)"
            menu.addItem(NSMenuItem(title: streamerStr, action: nil, keyEquivalent: ""))
        } else {
            let noStreamer = NSMenuItem(title: "📺 Watching: None", action: nil, keyEquivalent: "")
            noStreamer.isEnabled = false
            menu.addItem(noStreamer)
        }

        menu.addItem(NSMenuItem.separator())

        // 5. Mining Controls
        if minerState == "IDLE" {
            menu.addItem(NSMenuItem(title: "▶ Resume Mining", action: #selector(resumeMining), keyEquivalent: "r"))
        } else {
            menu.addItem(NSMenuItem(title: "⏸ Pause Mining", action: #selector(pauseMining), keyEquivalent: "p"))
        }
        menu.addItem(NSMenuItem(title: "⏩ Switch Streamer", action: #selector(switchStreamer), keyEquivalent: "s"))
        menu.addItem(NSMenuItem(title: "🎁 Claim All Drops", action: #selector(claimDropsAction), keyEquivalent: "c"))
        menu.addItem(NSMenuItem(title: "🔄 Reload Campaigns", action: #selector(reloadCampaigns), keyEquivalent: ""))

        menu.addItem(NSMenuItem.separator())

        // 6. Settings & Config
        menu.addItem(NSMenuItem(title: "⚙️ Connection Settings (Local / VPS)...", action: #selector(showConnectionSettings), keyEquivalent: ","))
        menu.addItem(NSMenuItem(title: "🔑 Set Twitch Auth Token...", action: #selector(promptSetAuthToken), keyEquivalent: "t"))

        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "❌ Quit", action: #selector(quitApp), keyEquivalent: "q"))

        statusItem.menu = menu
    }

    func renderProgressBar(percent: Double, length: Int = 10) -> String {
        let filled = max(0, min(length, Int(round(Double(length) * (percent / 100.0)))))
        let empty = length - filled
        let fillStr = String(repeating: "█", count: filled)
        let emptyStr = String(repeating: "░", count: empty)
        return "[\(fillStr)\(emptyStr)]"
    }

    func updateUI() {
        if let button = statusItem.button {
            if let game = activeGame, activeDropPercent > 0 {
                button.title = "⛏️ \(Int(activeDropPercent))% \(game)"
            } else if minerState == "IDLE" {
                button.title = "⛏️ Idle"
            } else if minerState == "OFFLINE" {
                button.title = "⛏️ Offline"
            } else {
                button.title = "⛏️ Drops"
            }
        }
        buildMenu()

        // Update main window title with status
        let modeText = isRemote ? "Remote [\(effectiveURL.host ?? "VPS")]" : "Local"
        mainWindow.title = "Twitch Drops Miner Enhanced — \(statusText) (\(modeText))"
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
        var targetURL = effectiveURL
        if !apiKey.isEmpty {
            var comps = URLComponents(url: targetURL, resolvingAgainstBaseURL: false)
            var items = comps?.queryItems ?? []
            items.append(URLQueryItem(name: "key", value: apiKey))
            comps?.queryItems = items
            if let u = comps?.url {
                targetURL = u
            }
        }

        var request = URLRequest(url: targetURL)
        if !apiKey.isEmpty {
            request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        }
        webView.load(request)
        connectWebSocket()
    }

    func connectWebSocket() {
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        var wsURLComponents = URLComponents(url: effectiveURL, resolvingAgainstBaseURL: false)
        wsURLComponents?.scheme = (effectiveURL.scheme == "https") ? "wss" : "ws"
        wsURLComponents?.path = "/ws"
        if !apiKey.isEmpty {
            wsURLComponents?.queryItems = [URLQueryItem(name: "key", value: apiKey)]
        }

        guard let wsURL = wsURLComponents?.url else { return }
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
        guard let data = text.data(using: .utf8),
              let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let event = json["event"] as? String else { return }

        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            let payload = json["data"]

            if event == "init", let state = payload as? [String: Any] {
                self.parseStateJSON(state)
            } else if event == "status", let s = payload as? [String: Any] {
                self.statusText = s["status"] as? String ?? self.statusText
                self.minerState = s["state"] as? String ?? self.minerState
                self.updateUI()
            } else if event == "drop" {
                if let drop = payload as? [String: Any] {
                    self.activeDropName = drop["name"] as? String
                    self.activeGame = drop["game"] as? String
                    self.activeDropPercent = drop["percentage"] as? Double ?? 0.0
                    let curr = drop["current_minutes"] as? Int ?? 0
                    let req = drop["required_minutes"] as? Int ?? 60
                    self.activeDropRemaining = drop["remaining_minutes"] as? Int ?? 0
                    self.activeDropMinutes = "\(curr)/\(req)m"
                } else {
                    self.activeDropName = nil
                    self.activeGame = nil
                    self.activeDropPercent = 0.0
                    self.activeDropMinutes = nil
                }
                self.updateUI()
            } else if event == "watching" {
                if let ch = payload as? [String: Any] {
                    self.activeChannel = ch["name"] as? String
                    self.activeViewers = ch["viewers"] as? Int ?? 0
                } else {
                    self.activeChannel = nil
                    self.activeViewers = 0
                }
                self.updateUI()
            }
        }
    }

    func pollStatus() {
        guard let url = URL(string: "\(effectiveURL.absoluteString)/api/status") else { return }
        var req = URLRequest(url: url)
        if !apiKey.isEmpty {
            req.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        }
        URLSession.shared.dataTask(with: req) { [weak self] data, _, error in
            guard let data = data, error == nil,
                  let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else {
                DispatchQueue.main.async {
                    if self?.minerState != "OFFLINE" {
                        self?.minerState = "OFFLINE"
                        self?.statusText = "Backend not reachable"
                        self?.updateUI()
                    }
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
        minerState = json["state"] as? String ?? "IDLE"
        statusText = json["status_text"] as? String ?? "Idle"
        isLoggedIn = json["is_logged_in"] as? Bool ?? false

        if let drop = json["active_drop"] as? [String: Any] {
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
        }

        if let ch = json["active_channel"] as? [String: Any] {
            activeChannel = ch["name"] as? String
            activeViewers = ch["viewers"] as? Int ?? 0
        } else {
            activeChannel = nil
            activeViewers = 0
        }

        updateUI()
    }

    // MARK: - Actions
    func sendPostRequest(endpoint: String, json: [String: Any]? = nil) {
        guard let url = URL(string: "\(effectiveURL.absoluteString)\(endpoint)") else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if !apiKey.isEmpty {
            request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
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

    @objc func promptSetAuthToken() {
        let alert = NSAlert()
        alert.messageText = "Set Twitch Auth Token"
        alert.informativeText = "Paste your 30-character 'auth-token' cookie from twitch.tv to log in directly:"
        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 320, height: 24))
        input.placeholderString = "abcdef0123456789..."
        alert.accessoryView = input
        alert.addButton(withTitle: "Save Token")
        alert.addButton(withTitle: "Cancel")

        if alert.runModal() == .alertFirstButtonReturn {
            let token = input.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            if !token.isEmpty {
                sendPostRequest(endpoint: "/api/auth/token", json: ["token": token])
            }
        }
    }

    // MARK: - Connection Settings Modal (Local vs Remote VPS)
    @objc func showConnectionSettings() {
        let alert = NSAlert()
        alert.messageText = "Connection Settings"
        alert.informativeText = "Configure whether this app connects to a local miner or a remote VPS Docker container:"
        alert.alertStyle = .informational

        let container = NSView(frame: NSRect(x: 0, y: 0, width: 420, height: 160))

        // Mode Segmented Control
        let modeLabel = NSTextField(labelWithString: "Connection Mode:")
        modeLabel.frame = NSRect(x: 0, y: 130, width: 140, height: 20)
        modeLabel.font = NSFont.boldSystemFont(ofSize: 12)
        container.addSubview(modeLabel)

        let modeSegment = NSSegmentedControl(labels: ["Local Daemon", "Remote VPS / Docker"], trackingMode: .selectOne, target: nil, action: nil)
        modeSegment.frame = NSRect(x: 140, y: 128, width: 270, height: 24)
        modeSegment.selectedSegment = isRemote ? 1 : 0
        container.addSubview(modeSegment)

        // Remote URL
        let urlLabel = NSTextField(labelWithString: "Server URL:")
        urlLabel.frame = NSRect(x: 0, y: 88, width: 140, height: 20)
        container.addSubview(urlLabel)

        let urlInput = NSTextField(frame: NSRect(x: 140, y: 85, width: 270, height: 24))
        urlInput.stringValue = serverURLString
        urlInput.placeholderString = "http://100.77.235.18:23432 or https://..."
        container.addSubview(urlInput)

        // API Key
        let keyLabel = NSTextField(labelWithString: "API Key (Optional):")
        keyLabel.frame = NSRect(x: 0, y: 48, width: 140, height: 20)
        container.addSubview(keyLabel)

        let keyInput = NSSecureTextField(frame: NSRect(x: 140, y: 45, width: 270, height: 24))
        keyInput.stringValue = apiKey
        keyInput.placeholderString = "Leave empty if not required"
        container.addSubview(keyInput)

        let hint = NSTextField(labelWithString: "Local uses http://127.0.0.1:23432. Remote connects to your Docker container.")
        hint.frame = NSRect(x: 0, y: 8, width: 420, height: 28)
        hint.font = NSFont.systemFont(ofSize: 11)
        hint.textColor = NSColor.secondaryLabelColor
        container.addSubview(hint)

        alert.accessoryView = container
        alert.addButton(withTitle: "Save & Connect")
        alert.addButton(withTitle: "Cancel")

        if alert.runModal() == .alertFirstButtonReturn {
            let newIsRemote = (modeSegment.selectedSegment == 1)
            let newURL = urlInput.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
            let newKey = keyInput.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)

            self.isRemote = newIsRemote
            self.serverURLString = newURL.isEmpty ? (newIsRemote ? "http://vps:23432" : "http://127.0.0.1:23432") : newURL
            self.apiKey = newKey

            print("Updated connection: isRemote=\(self.isRemote), url=\(self.serverURLString)")
            reloadConnection()
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
