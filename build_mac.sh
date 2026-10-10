#!/bin/bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_NAME="TwitchDropsMinerEnhanced"
APP_BUNDLE="$DIR/$APP_NAME.app"
MACOS_DIR="$APP_BUNDLE/Contents/MacOS"
RESOURCES_DIR="$APP_BUNDLE/Contents/Resources"

echo "==> Building Twitch Drops Miner Enhanced macOS Menu Bar App..."

mkdir -p "$MACOS_DIR"
mkdir -p "$RESOURCES_DIR"

# 1. Compile Swift native menu bar application
echo "==> Compiling Swift menu bar binary..."
swiftc -O "$DIR/mac/main.swift" -o "$MACOS_DIR/TwitchDropsMinerMenu"
chmod +x "$MACOS_DIR/TwitchDropsMinerMenu"

# 2. Generate Info.plist (LSUIElement=true for native menu bar extra)
echo "==> Generating Info.plist..."
cat << 'EOF' > "$APP_BUNDLE/Contents/Info.plist"
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>TwitchDropsMinerMenu</string>
    <key>CFBundleIdentifier</key>
    <string>dev.brasilio.TwitchDropsMinerEnhanced</string>
    <key>CFBundleName</key>
    <string>Twitch Drops Miner Enhanced</string>
    <key>CFBundleDisplayName</key>
    <string>Twitch Drops Miner Enhanced</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.0.0</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key>
    <true/>
</dict>
</plist>
EOF

echo "==> Successfully created $APP_BUNDLE!"
echo "==> You can run it with: open \"$APP_BUNDLE\""
