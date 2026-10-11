// swift-tools-version:5.10
// Unit tests for the native shell's pure logic: `swift test --package-path mac`.
// The app itself is built by build_mac.sh, which compiles main.swift together with Sources/MinerCore.
import PackageDescription

let package = Package(
    name: "TwitchDropsMinerMenu",
    platforms: [.macOS(.v13)],
    targets: [
        .target(name: "MinerCore", path: "Sources/MinerCore"),
        .testTarget(name: "MinerCoreTests", dependencies: ["MinerCore"], path: "Tests/MinerCoreTests"),
    ]
)
