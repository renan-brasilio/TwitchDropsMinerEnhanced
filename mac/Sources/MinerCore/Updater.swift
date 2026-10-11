import Foundation
import CryptoKit

/// Where the project lives; the About section and the updater both point here.
enum ProjectLinks {
    static let repository = "renan-brasilio/TwitchDropsMinerEnhanced"
    static let repositoryURL = URL(string: "https://github.com/\(repository)")!
    static let releasesURL = URL(string: "https://github.com/\(repository)/releases")!
    static let issuesURL = URL(string: "https://github.com/\(repository)/issues")!
    static let latestReleaseAPI = URL(string: "https://api.github.com/repos/\(repository)/releases/latest")!
}

/// The newest published release and the macOS archive the updater installs from it.
struct ReleaseInfo: Equatable {
    let version: String
    let pageURL: URL
    let archiveURL: URL
    /// Lowercase hex SHA-256 that GitHub reports for the archive.
    let sha256: String
}

enum Release {
    static let archiveSuffix = "-macOS-arm64.zip"
    /// build_mac.sh zips the app under this name, whatever the installed copy is called.
    static let archivedAppName = "TwitchDropsMinerEnhanced.app"

    /// Reads GitHub's `releases/latest` JSON. Returns nil unless it has a macOS archive with a
    /// SHA-256 digest served from github.com over HTTPS, since nothing else can be installed safely.
    static func parse(_ data: Data) -> ReleaseInfo? {
        guard let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              json["draft"] as? Bool != true, json["prerelease"] as? Bool != true,
              let tag = json["tag_name"] as? String,
              let page = (json["html_url"] as? String).flatMap(URL.init(string:)),
              let assets = json["assets"] as? [[String: Any]] else { return nil }
        let version = tag.hasPrefix("v") ? String(tag.dropFirst()) : tag
        guard components(version) != nil else { return nil }
        for asset in assets {
            guard let name = asset["name"] as? String, name.hasSuffix(archiveSuffix),
                  let url = (asset["browser_download_url"] as? String).flatMap(URL.init(string:)),
                  url.scheme == "https", url.host == "github.com",
                  let digest = asset["digest"] as? String, digest.hasPrefix("sha256:") else { continue }
            let hex = digest.dropFirst("sha256:".count).lowercased()
            guard hex.count == 64, hex.allSatisfy(\.isHexDigit) else { continue }
            return ReleaseInfo(version: version, pageURL: page, archiveURL: url, sha256: hex)
        }
        return nil
    }

    /// True when `candidate` is a strictly higher dotted version than `current` ("1.10.0" > "1.9.2").
    static func isNewer(_ candidate: String, than current: String) -> Bool {
        guard let new = components(candidate), let old = components(current) else { return false }
        for index in 0..<max(new.count, old.count) {
            let a = index < new.count ? new[index] : 0
            let b = index < old.count ? old[index] : 0
            if a != b { return a > b }
        }
        return false
    }

    private static func components(_ version: String) -> [Int]? {
        let parts = version.split(separator: ".", omittingEmptySubsequences: false).map { Int($0) }
        guard !parts.isEmpty, parts.allSatisfy({ ($0 ?? -1) >= 0 }) else { return nil }
        return parts.map { $0! }
    }
}

/// What the updater is doing, as shown in Settings → About and the menus.
enum UpdateState: Equatable {
    case idle
    case checking
    case upToDate(latest: String)
    case available(ReleaseInfo)
    case installing(ReleaseInfo)
    case failed(String)

    var availableRelease: ReleaseInfo? {
        if case .available(let release) = self { return release }
        return nil
    }

    var payload: [String: Any] {
        switch self {
        case .idle: return ["state": "idle"]
        case .checking: return ["state": "checking"]
        case .upToDate(let latest): return ["state": "upToDate", "latestVersion": latest]
        case .available(let release):
            return ["state": "available", "latestVersion": release.version, "releaseURL": release.pageURL.absoluteString]
        case .installing(let release):
            return ["state": "installing", "latestVersion": release.version, "releaseURL": release.pageURL.absoluteString]
        case .failed(let message): return ["state": "failed", "message": message]
        }
    }
}

struct UpdateError: Error, Equatable {
    let message: String
}

/// Verifies a downloaded release archive and swaps it in for the running app.
struct UpdateInstaller {
    let bundleURL: URL
    let bundleIdentifier: String

    /// Checks the archive digest, extracts it into `workDir`, and returns the new app bundle once
    /// its identifier and version match what the release promised.
    func prepare(archive: URL, release: ReleaseInfo, workDir: URL) throws -> URL {
        let data = try Data(contentsOf: archive)
        let digest = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
        guard digest == release.sha256 else {
            throw UpdateError(message: "The download doesn't match the release checksum.")
        }
        let extracted = workDir.appendingPathComponent("extracted", isDirectory: true)
        try? FileManager.default.removeItem(at: extracted)
        let ditto = Process()
        ditto.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        ditto.arguments = ["-x", "-k", archive.path, extracted.path]
        try ditto.run()
        ditto.waitUntilExit()
        guard ditto.terminationStatus == 0 else {
            throw UpdateError(message: "The update archive couldn't be extracted.")
        }
        let app = extracted.appendingPathComponent(Release.archivedAppName, isDirectory: true)
        let plist = app.appendingPathComponent("Contents/Info.plist")
        guard let info = NSDictionary(contentsOf: plist),
              info["CFBundleIdentifier"] as? String == bundleIdentifier,
              info["CFBundleShortVersionString"] as? String == release.version else {
            throw UpdateError(message: "The update archive doesn't contain this app at v\(release.version).")
        }
        return app
    }

    /// Fails early when the app's folder can't be written (e.g. it runs from a read-only disk image).
    func checkReplaceable() throws {
        let folder = bundleURL.deletingLastPathComponent().path
        guard FileManager.default.isWritableFile(atPath: folder),
              FileManager.default.isWritableFile(atPath: bundleURL.path) else {
            throw UpdateError(message: "Can't replace the app in \(folder). Move it to Applications or install the update manually.")
        }
    }

    /// Shell script run once the app has quit: moves the old bundle aside, copies the new one in,
    /// puts the old one back if that fails, then relaunches whichever is in place.
    func swapScript(waitingFor pid: Int32, newApp: URL, backup: URL, relaunch: Bool = true) -> String {
        let target = quote(bundleURL.path)
        var lines = [
            "while kill -0 \(pid) 2>/dev/null; do sleep 0.2; done",
            "rm -rf \(quote(backup.path))",
            "mv \(target) \(quote(backup.path)) || exit 1",
            "if /usr/bin/ditto \(quote(newApp.path)) \(target); then rm -rf \(quote(backup.path)); "
                + "else rm -rf \(target); mv \(quote(backup.path)) \(target); fi",
        ]
        if relaunch { lines.append("/usr/bin/open \(target)") }
        return lines.joined(separator: "\n")
    }

    private func quote(_ path: String) -> String {
        "'" + path.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}
