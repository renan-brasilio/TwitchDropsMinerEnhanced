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

    var autoCheckUpdates: Bool {
        get { UserDefaults.standard.object(forKey: "autoCheckUpdates") as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: "autoCheckUpdates") }
    }
    var appVersion: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
    }
    var updateState: UpdateState = .idle {
        didSet {
            buildMenu()
            setupMainMenu() // the app menu carries the same update item
            publishNativeSettings()
        }
    }
    var updateTimer: Timer?

    // State cache
    var status = MinerStatus()
    var hasLoadedDashboard: Bool = false
    var isTerminatingExplicitly: Bool = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        ProcessInfo.processInfo.disableAutomaticTermination("Twitch Drops Miner menu bar extra stays active")

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

        // 7. Look for a newer release now and once a day (only when enabled)
        updateTimer = Timer.scheduledTimer(withTimeInterval: 24 * 3600, repeats: true) { [weak self] _ in
            guard let self, self.autoCheckUpdates else { return }
            self.checkForUpdates(userInitiated: false)
        }
        if autoCheckUpdates { checkForUpdates(userInitiated: false) }

        // Make app active and show window
        NSApp.activate(ignoringOtherApps: true)
        mainWindow.makeKeyAndOrderFront(nil)
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        showMainWindow()
        return true
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        return false
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if isTerminatingExplicitly || !keepRunningOnClose {
            return .terminateNow
        }
        closeToMenuBar()
        return .terminateCancel
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

    // Links with target="_blank" and window.open() ask for a new web view; open them in the
    // user's browser instead (http/https only), since the dashboard window is single-page.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = navigationAction.request.url, ["http", "https"].contains(url.scheme?.lowercased() ?? "") {
            NSWorkspace.shared.open(url)
        }
        return nil
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
            app: .init(version: appVersion, autoCheckUpdates: autoCheckUpdates, update: updateState),
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
        case .preferences(let keepRunning, let style, let autoCheck):
            if let keepRunning { keepRunningOnClose = keepRunning }
            if let style { menuBarStyle = style }
            if let autoCheck {
                autoCheckUpdates = autoCheck
                if autoCheck, case .idle = updateState { checkForUpdates(userInitiated: false) }
            }
            setupMainMenu()
            updateUI()
            publishNativeSettings()
        case .update(.check):
            checkForUpdates(userInitiated: false) // the About section shows the result
        case .update(.install):
            installUpdate()
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

    @objc func showAbout() {
        showMainWindow()
        webView.evaluateJavaScript("switchTab('settings'); document.getElementById('aboutSettings')?.scrollIntoView()", completionHandler: nil)
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
        appMenu.addItem(NSMenuItem(title: "About Twitch Drops Miner Enhanced", action: #selector(showAbout), keyEquivalent: ""))
        appMenu.addItem(StatusMenuBuilder.updateItem(for: updateState, target: self))
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
        statusItem.menu = StatusMenuBuilder.buildMenu(
            status: status,
            isRemote: isRemote,
            menuBarStyle: menuBarStyle,
            keepRunningOnClose: keepRunningOnClose,
            update: updateState,
            target: self
        )
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

    // MARK: - Updates (GitHub Releases)
    @objc func checkForUpdatesAction() { checkForUpdates(userInitiated: true) }
    @objc func installUpdateAction() { installUpdate() }

    /// Asks GitHub for the latest release. Menu-initiated checks report the result in an alert;
    /// automatic and Settings checks only update the menus and Settings → About.
    func checkForUpdates(userInitiated: Bool) {
        switch updateState {
        case .checking, .installing: return
        default: break
        }
        updateState = .checking
        var request = URLRequest(url: ProjectLinks.latestReleaseAPI)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        request.timeoutInterval = 15
        URLSession.shared.dataTask(with: request) { [weak self] data, response, error in
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            let release = (error == nil && code == 200) ? data.flatMap(Release.parse) : nil
            DispatchQueue.main.async {
                guard let self else { return }
                if let release {
                    self.updateState = Release.isNewer(release.version, than: self.appVersion)
                        ? .available(release) : .upToDate(latest: release.version)
                } else if let error {
                    self.updateState = .failed("Couldn't reach GitHub: \(error.localizedDescription)")
                } else {
                    self.updateState = .failed("GitHub returned no installable release (HTTP \(code)).")
                }
                if userInitiated { self.showUpdateAlert() }
            }
        }.resume()
    }

    func showUpdateAlert() {
        let alert = NSAlert()
        switch updateState {
        case .available(let release):
            alert.messageText = "Version \(release.version) is available"
            alert.informativeText = "You have \(appVersion). The app downloads the update from GitHub, checks it, and restarts."
            alert.addButton(withTitle: "Install and Restart")
            alert.addButton(withTitle: "Release Notes")
            alert.addButton(withTitle: "Later")
            showMainWindow()
            switch alert.runModal() {
            case .alertFirstButtonReturn: installUpdate()
            case .alertSecondButtonReturn: NSWorkspace.shared.open(release.pageURL)
            default: break
            }
            return
        case .upToDate(let latest):
            alert.messageText = "You're up to date"
            alert.informativeText = "Version \(appVersion) is the latest (v\(latest))."
        case .failed(let message):
            alert.alertStyle = .warning
            alert.messageText = "Couldn't check for updates"
            alert.informativeText = message
        default:
            return
        }
        showMainWindow()
        alert.runModal()
    }

    /// Downloads the release archive, verifies it, then quits so a helper script can swap the
    /// bundle and relaunch. Any failure leaves the current app untouched.
    func installUpdate() {
        guard let release = updateState.availableRelease else { return }
        let installer = UpdateInstaller(bundleURL: Bundle.main.bundleURL,
                                        bundleIdentifier: Bundle.main.bundleIdentifier ?? "")
        do {
            try installer.checkReplaceable()
        } catch {
            updateState = .failed((error as? UpdateError)?.message ?? error.localizedDescription)
            return
        }
        updateState = .installing(release)
        URLSession.shared.downloadTask(with: release.archiveURL) { [weak self] location, response, error in
            let work = FileManager.default.temporaryDirectory
                .appendingPathComponent("TwitchDropsMinerEnhanced-update-\(UUID().uuidString)", isDirectory: true)
            let result: Result<URL, UpdateError>
            do {
                guard error == nil, let location, (response as? HTTPURLResponse)?.statusCode == 200 else {
                    throw UpdateError(message: "Download failed: \(error?.localizedDescription ?? "HTTP \((response as? HTTPURLResponse)?.statusCode ?? 0)")")
                }
                try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
                let archive = work.appendingPathComponent("update.zip")
                try FileManager.default.moveItem(at: location, to: archive)
                result = .success(try installer.prepare(archive: archive, release: release, workDir: work))
            } catch {
                result = .failure(error as? UpdateError ?? UpdateError(message: error.localizedDescription))
            }
            DispatchQueue.main.async {
                self?.finishInstall(result, installer: installer, backup: work.appendingPathComponent("previous.app"))
            }
        }.resume()
    }

    func finishInstall(_ result: Result<URL, UpdateError>, installer: UpdateInstaller, backup: URL) {
        switch result {
        case .failure(let error):
            updateState = .failed(error.message)
        case .success(let newApp):
            let swap = Process()
            swap.executableURL = URL(fileURLWithPath: "/bin/sh")
            swap.arguments = ["-c", installer.swapScript(
                waitingFor: ProcessInfo.processInfo.processIdentifier, newApp: newApp, backup: backup)]
            swap.standardOutput = FileHandle.nullDevice
            swap.standardError = FileHandle.nullDevice
            do {
                try swap.run()
                isTerminatingExplicitly = true
                NSApp.terminate(nil)
            } catch {
                updateState = .failed("Couldn't start the installer: \(error.localizedDescription)")
            }
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
        isTerminatingExplicitly = true
        NSApplication.shared.terminate(nil)
    }
}

// App Entry Point
let app = NSApplication.shared
app.setActivationPolicy(.regular) // Full native macOS application with Dock icon and Window!
let delegate = AppDelegate()
app.delegate = delegate
app.run()
