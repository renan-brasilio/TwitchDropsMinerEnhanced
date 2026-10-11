import AppKit
import CryptoKit
import Foundation
import Testing
@testable import MinerCore

private let digest = String(repeating: "ab", count: 32)

private func releaseJSON(tag: String = "v1.9.0", assets: [[String: Any]]? = nil,
                         draft: Bool = false, prerelease: Bool = false) -> Data {
    let defaultAssets: [[String: Any]] = [
        ["name": "TwitchDropsMinerEnhanced-\(tag)-macOS-arm64.dmg", "digest": "sha256:\(digest)",
         "browser_download_url": "https://github.com/o/r/releases/download/\(tag)/a.dmg"],
        ["name": "TwitchDropsMinerEnhanced-\(tag)-macOS-arm64.zip", "digest": "sha256:\(digest.uppercased())",
         "browser_download_url": "https://github.com/o/r/releases/download/\(tag)/a.zip"],
    ]
    let json: [String: Any] = [
        "tag_name": tag, "html_url": "https://github.com/o/r/releases/tag/\(tag)",
        "draft": draft, "prerelease": prerelease, "assets": assets ?? defaultAssets,
    ]
    return try! JSONSerialization.data(withJSONObject: json)
}

@Suite("Release lookup")
struct ReleaseTests {
    @Test("The macOS zip and its digest are taken from the latest release")
    func parsesArchive() throws {
        let release = try #require(Release.parse(releaseJSON()))
        #expect(release.version == "1.9.0")
        #expect(release.archiveURL.absoluteString == "https://github.com/o/r/releases/download/v1.9.0/a.zip")
        #expect(release.sha256 == digest)
        #expect(release.pageURL.absoluteString == "https://github.com/o/r/releases/tag/v1.9.0")
    }

    @Test("Releases without a verifiable archive from github.com are not installable", arguments: [
        ["name": "x-macOS-arm64.zip", "browser_download_url": "https://github.com/a.zip"],                          // no digest
        ["name": "x-macOS-arm64.zip", "digest": "md5:abc", "browser_download_url": "https://github.com/a.zip"],
        ["name": "x-macOS-arm64.zip", "digest": "sha256:1234", "browser_download_url": "https://github.com/a.zip"],
        ["name": "x-macOS-arm64.zip", "digest": "sha256:\(String(repeating: "a", count: 64))", "browser_download_url": "http://github.com/a.zip"],
        ["name": "x-macOS-arm64.zip", "digest": "sha256:\(String(repeating: "a", count: 64))", "browser_download_url": "https://evil.example/a.zip"],
        ["name": "x-windows.zip", "digest": "sha256:\(String(repeating: "a", count: 64))", "browser_download_url": "https://github.com/a.zip"],
    ] as [[String: Any]])
    func rejectsUnverifiableAssets(asset: [String: Any]) {
        #expect(Release.parse(releaseJSON(assets: [asset])) == nil)
    }

    @Test("Drafts, prereleases, odd tags and non-release JSON are ignored")
    func rejectsOtherReleases() {
        #expect(Release.parse(releaseJSON(draft: true)) == nil)
        #expect(Release.parse(releaseJSON(prerelease: true)) == nil)
        #expect(Release.parse(releaseJSON(tag: "nightly")) == nil)
        #expect(Release.parse(Data(#"{"message": "API rate limit exceeded"}"#.utf8)) == nil)
    }

    @Test("Versions compare numerically, part by part", arguments: [
        ("1.8.0", "1.7.9", true), ("1.10.0", "1.9.2", true), ("2.0", "1.99.99", true), ("1.8.1", "1.8", true),
        ("1.8.0", "1.8.0", false), ("1.8", "1.8.0", false), ("1.7.9", "1.8.0", false), ("1.9.0", "x", false),
    ])
    func comparesVersions(candidate: String, current: String, newer: Bool) {
        #expect(Release.isNewer(candidate, than: current) == newer)
    }
}

@Suite("Update installer")
struct UpdateInstallerTests {
    let root: URL
    let target: URL
    let installer: UpdateInstaller

    init() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent("tdme-updater-\(UUID().uuidString)")
        target = root.appendingPathComponent("Applications/TwitchDropsMinerEnhanced.app")
        try Self.makeApp(at: target, id: "dev.example.app", version: "1.8.0")
        installer = UpdateInstaller(bundleURL: target, bundleIdentifier: "dev.example.app")
    }

    static func makeApp(at url: URL, id: String, version: String) throws {
        let contents = url.appendingPathComponent("Contents")
        try FileManager.default.createDirectory(at: contents, withIntermediateDirectories: true)
        try (["CFBundleIdentifier": id, "CFBundleShortVersionString": version] as NSDictionary)
            .write(to: contents.appendingPathComponent("Info.plist"))
    }

    /// A release zip like build_mac.sh makes, plus what GitHub would report for it.
    func archive(id: String = "dev.example.app", version: String = "1.9.0") throws -> (URL, ReleaseInfo) {
        let stage = root.appendingPathComponent("stage-\(UUID().uuidString)")
        try Self.makeApp(at: stage.appendingPathComponent("TwitchDropsMinerEnhanced.app"), id: id, version: version)
        let zip = root.appendingPathComponent("\(UUID().uuidString).zip")
        let ditto = Process()
        ditto.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
        ditto.arguments = ["-c", "-k", "--keepParent", stage.appendingPathComponent("TwitchDropsMinerEnhanced.app").path, zip.path]
        try ditto.run()
        ditto.waitUntilExit()
        let sha = SHA256.hash(data: try Data(contentsOf: zip)).map { String(format: "%02x", $0) }.joined()
        let release = ReleaseInfo(version: "1.9.0", pageURL: URL(string: "https://github.com/o/r")!,
                                  archiveURL: URL(string: "https://github.com/o/r/a.zip")!, sha256: sha)
        return (zip, release)
    }

    func version(at app: URL) -> String? {
        NSDictionary(contentsOf: app.appendingPathComponent("Contents/Info.plist"))?["CFBundleShortVersionString"] as? String
    }

    func runSwap(newApp: URL, backup: URL) throws {
        let shell = Process()
        shell.executableURL = URL(fileURLWithPath: "/bin/sh")
        shell.arguments = ["-c", installer.swapScript(waitingFor: 999_999, newApp: newApp, backup: backup, relaunch: false)]
        try shell.run()
        shell.waitUntilExit()
    }

    @Test("A verified archive is extracted, swapped in, and the old app removed")
    func installsVerifiedArchive() throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let (zip, release) = try archive()
        let work = root.appendingPathComponent("work")
        let newApp = try installer.prepare(archive: zip, release: release, workDir: work)
        let backup = work.appendingPathComponent("previous.app")
        try runSwap(newApp: newApp, backup: backup)
        #expect(version(at: target) == "1.9.0")
        #expect(!FileManager.default.fileExists(atPath: backup.path))
    }

    @Test("A failed copy puts the previous app back")
    func rollsBackFailedCopy() throws {
        defer { try? FileManager.default.removeItem(at: root) }
        try runSwap(newApp: root.appendingPathComponent("missing.app"), backup: root.appendingPathComponent("previous.app"))
        #expect(version(at: target) == "1.8.0")
    }

    @Test("Archives with a wrong checksum, another app, or another version are refused")
    func refusesMismatches() throws {
        defer { try? FileManager.default.removeItem(at: root) }
        let work = root.appendingPathComponent("work")
        let (zip, release) = try archive()
        let tampered = ReleaseInfo(version: release.version, pageURL: release.pageURL, archiveURL: release.archiveURL,
                                   sha256: String(repeating: "0", count: 64))
        #expect(throws: UpdateError.self) { try installer.prepare(archive: zip, release: tampered, workDir: work) }

        let (otherZip, otherRelease) = try archive(id: "dev.attacker.app")
        #expect(throws: UpdateError.self) { try installer.prepare(archive: otherZip, release: otherRelease, workDir: work) }

        let (oldZip, oldRelease) = try archive(version: "1.7.0")
        #expect(throws: UpdateError.self) { try installer.prepare(archive: oldZip, release: oldRelease, workDir: work) }
        #expect(version(at: target) == "1.8.0")
    }
}

@Suite("Update menu item")
@MainActor
struct UpdateMenuItemTests {
    let release = ReleaseInfo(version: "1.9.0", pageURL: URL(string: "https://github.com/o/r")!,
                              archiveURL: URL(string: "https://github.com/o/r/a.zip")!, sha256: digest)

    @Test("The item offers a check, then the install once a newer release is known")
    func titlesAndActions() {
        let target = NSObject()
        let check = StatusMenuBuilder.updateItem(for: .upToDate(latest: "1.8.0"), target: target)
        #expect(check.title == "Check for Updates…")
        #expect(check.action == MenuActions.checkForUpdates)

        let install = StatusMenuBuilder.updateItem(for: .available(release), target: target)
        #expect(install.title == "Install Update v1.9.0…")
        #expect(install.action == MenuActions.installUpdate)

        for busy in [UpdateState.checking, .installing(release)] {
            #expect(StatusMenuBuilder.updateItem(for: busy, target: target).action == nil)
        }
    }
}
