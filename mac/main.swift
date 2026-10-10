import AppKit
import Foundation

class AppDelegate: NSObject, NSApplicationDelegate, URLSessionWebSocketDelegate {
    var statusItem: NSStatusItem!
    var webSocketTask: URLSessionWebSocketTask?
    var pollTimer: Timer?
    var backendProcess: Process?

    var port: Int = 23432
    var minerState: String = "OFFLINE"
    var statusText: String = "Connecting to backend..."
    var activeGame: String? = nil
    var activeDropName: String? = nil
    var activeDropPercent: Double = 0.0
    var activeDropMinutes: String? = nil
    var activeDropRemaining: Int = 0
    var activeChannel: String? = nil
    var activeViewers: Int = 0
    var isLoggedIn: Bool = false
    var deviceCode: String? = nil
    var deviceUri: String? = nil

    func applicationDidFinishLaunching(_ notification: Notification) {
        // Setup Menu Bar Item
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        if let button = statusItem.button {
            button.title = "⛏️ Drops"
            button.toolTip = "Twitch Drops Miner Enhanced"
        }

        buildInitialMenu()
        checkOrStartBackend()

        // Start polling & WebSocket
        connectWebSocket()
        pollTimer = Timer.scheduledTimer(withTimeInterval: 3.0, repeats: true) { [weak self] _ in
            self?.pollStatus()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        webSocketTask?.cancel(with: .goingAway, reason: nil)
        if let proc = backendProcess, proc.isRunning {
            proc.terminate()
        }
    }

    // MARK: - Backend Process Management
    func checkOrStartBackend() {
        let url = URL(string: "http://127.0.0.1:\(port)/api/status")!
        var request = URLRequest(url: url)
        request.timeoutInterval = 1.0

        URLSession.shared.dataTask(with: request) { [weak self] _, response, error in
            if error != nil {
                // Backend not responding, attempt to launch it
                DispatchQueue.main.async {
                    self?.spawnBackend()
                }
            }
        }.resume()
    }

    func spawnBackend() {
        let fileManager = FileManager.default
        let possibleDirs = [
            Bundle.main.bundleURL.deletingLastPathComponent().path,
            (Bundle.main.bundleURL.deletingLastPathComponent().path as NSString).appendingPathComponent(".."),
            "/Users/renanrocha/tmp/TwitchDropsMinerEnhanced",
            FileManager.default.currentDirectoryPath
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

        guard let script = scriptPath else {
            print("Could not locate main.py")
            return
        }

        let pythonExec = venvPython ?? (fileManager.fileExists(atPath: "/opt/homebrew/bin/python3.14") ? "/opt/homebrew/bin/python3.14" : "/usr/bin/python3")

        let process = Process()
        process.executableURL = URL(fileURLWithPath: pythonExec)
        process.arguments = [script]
        process.currentDirectoryURL = URL(fileURLWithPath: (script as NSString).deletingLastPathComponent)

        do {
            try process.run()
            self.backendProcess = process
            print("Spawned backend process with PID \(process.processIdentifier)")
        } catch {
            print("Failed to spawn backend process: \(error)")
        }
    }

    // MARK: - WebSocket & Polling
    func connectWebSocket() {
        let url = URL(string: "ws://127.0.0.1:\(port)/ws")!
        let session = URLSession(configuration: .default, delegate: self, delegateQueue: nil)
        webSocketTask = session.webSocketTask(with: url)
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
                // Reconnect after delay
                DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) {
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
            } else if event == "auth_code", let codeData = payload as? [String: Any] {
                self.deviceCode = codeData["code"] as? String
                self.deviceUri = codeData["uri"] as? String
                self.updateUI()
            }
        }
    }

    func pollStatus() {
        guard let url = URL(string: "http://127.0.0.1:\(port)/api/status") else { return }
        URLSession.shared.dataTask(with: url) { [weak self] data, _, error in
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

        if let dc = json["device_code"] as? [String: Any] {
            deviceCode = dc["code"] as? String
            deviceUri = dc["uri"] as? String
        } else {
            deviceCode = nil
            deviceUri = nil
        }

        updateUI()
    }

    // MARK: - UI & Menu Bar Rendering
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
    }

    func buildInitialMenu() {
        let menu = NSMenu()
        menu.addItem(NSMenuItem(title: "Twitch Drops Miner Enhanced", action: nil, keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Status: Connecting...", action: nil, keyEquivalent: ""))
        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Open Dashboard...", action: #selector(openDashboard), keyEquivalent: "d"))
        menu.addItem(NSMenuItem(title: "Quit", action: #selector(quitApp), keyEquivalent: "q"))
        statusItem.menu = menu
    }

    func buildMenu() {
        let menu = NSMenu()

        // Title Header
        let titleItem = NSMenuItem(title: "Twitch Drops Miner Enhanced", action: nil, keyEquivalent: "")
        titleItem.attributedTitle = NSAttributedString(
            string: "Twitch Drops Miner Enhanced",
            attributes: [.font: NSFont.boldSystemFont(ofSize: 13)]
        )
        menu.addItem(titleItem)

        // Status Item with Color Indicator
        let statusDot: String
        switch minerState {
        case "IDLE": statusDot = "🟡 Idle"
        case "OFFLINE": statusDot = "🔴 Backend Offline"
        case "CHANNEL_SWITCH", "CHANNELS_FETCH", "INVENTORY_FETCH": statusDot = "🔄 Updating"
        default: statusDot = "🟢 Mining Active"
        }
        let statusMenuItem = NSMenuItem(title: "Status: \(statusDot) — \(statusText)", action: nil, keyEquivalent: "")
        menu.addItem(statusMenuItem)

        menu.addItem(NSMenuItem.separator())

        // Active Drop Section
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

        // Active Streamer Section
        if let ch = activeChannel {
            let streamerStr = "📺 Watching: \(ch) (\(activeViewers.formatted()) viewers)"
            let streamerItem = NSMenuItem(title: streamerStr, action: nil, keyEquivalent: "")
            menu.addItem(streamerItem)
        } else {
            let noStreamer = NSMenuItem(title: "📺 Watching: None", action: nil, keyEquivalent: "")
            noStreamer.isEnabled = false
            menu.addItem(noStreamer)
        }

        // Activation Prompt if Device Code is required
        if let code = deviceCode {
            menu.addItem(NSMenuItem.separator())
            let alertItem = NSMenuItem(title: "⚠️ Activation Code: \(code)", action: #selector(openActivation), keyEquivalent: "")
            alertItem.attributedTitle = NSAttributedString(
                string: "⚠️ Activation Code: \(code) (Click to Activate)",
                attributes: [.foregroundColor: NSColor.systemOrange, .font: NSFont.boldSystemFont(ofSize: 12)]
            )
            menu.addItem(alertItem)
        }

        menu.addItem(NSMenuItem.separator())

        // Action Controls
        if minerState == "IDLE" {
            menu.addItem(NSMenuItem(title: "▶ Resume Mining", action: #selector(resumeMining), keyEquivalent: "r"))
        } else {
            menu.addItem(NSMenuItem(title: "⏸ Pause Mining", action: #selector(pauseMining), keyEquivalent: "p"))
        }
        menu.addItem(NSMenuItem(title: "⏩ Switch Streamer", action: #selector(switchStreamer), keyEquivalent: "s"))
        menu.addItem(NSMenuItem(title: "🎁 Claim All Drops", action: #selector(claimDropsAction), keyEquivalent: "c"))
        menu.addItem(NSMenuItem(title: "🔄 Reload Campaigns", action: #selector(reloadCampaigns), keyEquivalent: ""))

        menu.addItem(NSMenuItem.separator())

        // Quick Links & Config
        menu.addItem(NSMenuItem(title: "🌐 Open Web Dashboard...", action: #selector(openDashboard), keyEquivalent: "d"))
        menu.addItem(NSMenuItem(title: "🔑 Set Twitch Auth Token...", action: #selector(promptSetAuthToken), keyEquivalent: "t"))

        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "❌ Quit Twitch Drops Miner", action: #selector(quitApp), keyEquivalent: "q"))

        statusItem.menu = menu
    }

    // MARK: - Actions
    @objc func openDashboard() {
        if let url = URL(string: "http://127.0.0.1:\(port)") {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func openActivation() {
        let uri = deviceUri ?? "https://www.twitch.tv/activate"
        if let url = URL(string: uri) {
            NSWorkspace.shared.open(url)
        }
    }

    @objc func pauseMining() {
        sendPostRequest(endpoint: "/api/control/pause")
    }

    @objc func resumeMining() {
        sendPostRequest(endpoint: "/api/control/resume")
    }

    @objc func switchStreamer() {
        sendPostRequest(endpoint: "/api/control/switch")
    }

    @objc func reloadCampaigns() {
        sendPostRequest(endpoint: "/api/control/reload")
    }

    @objc func claimDropsAction() {
        sendPostRequest(endpoint: "/api/control/claim")
    }

    @objc func promptSetAuthToken() {
        let alert = NSAlert()
        alert.messageText = "Set Twitch Auth Token"
        alert.informativeText = "Paste your 30-character 'auth-token' cookie from twitch.tv to log in directly:"
        alert.alertStyle = .informational

        let input = NSTextField(frame: NSRect(x: 0, y: 0, width: 300, height: 24))
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

    func sendPostRequest(endpoint: String, json: [String: Any]? = nil) {
        guard let url = URL(string: "http://127.0.0.1:\(port)\(endpoint)") else { return }
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")

        if let json = json, let data = try? JSONSerialization.data(withJSONObject: json) {
            request.httpBody = data
        }

        URLSession.shared.dataTask(with: request) { [weak self] _, _, _ in
            self?.pollStatus()
        }.resume()
    }

    @objc func quitApp() {
        NSApplication.shared.terminate(nil)
    }
}

// Application Entry Point
let app = NSApplication.shared
app.setActivationPolicy(.accessory) // Runs as a clean menu bar extra without docking icon
let delegate = AppDelegate()
app.delegate = delegate
app.run()
