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
swiftc -O "$DIR/mac/main.swift" "$DIR"/mac/Sources/MinerCore/*.swift -o "$MACOS_DIR/TwitchDropsMinerMenu"
chmod +x "$MACOS_DIR/TwitchDropsMinerMenu"

# 2. Copy AppIcon.icns and MenuBarIcon.png
if [ -f "$DIR/AppIcon.icns" ]; then
    echo "==> Copying AppIcon.icns to Resources..."
    cp "$DIR/AppIcon.icns" "$RESOURCES_DIR/AppIcon.icns"
fi
if [ -f "$DIR/icons/MenuBarIcon.png" ]; then
    echo "==> Copying MenuBarIcon.png to Resources..."
    cp "$DIR/icons/MenuBarIcon.png" "$RESOURCES_DIR/MenuBarIcon.png"
    cp "$DIR/icons/MenuBarIcon@2x.png" "$RESOURCES_DIR/MenuBarIcon@2x.png"
fi

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
    <string>1.6.0</string>
    <key>CFBundleVersion</key>
    <string>1.6.0</string>
    <key>CFBundleIconFile</key>
    <string>AppIcon</string>
    <key>CFBundleIconName</key>
    <string>AppIcon</string>
    <key>NSHighResolutionCapable</key>
    <true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key>
    <true/>
</dict>
</plist>
EOF

echo "==> Successfully created $APP_BUNDLE!"

# 3. Package drag-and-drop installer DMG (artifact names carry the version tag)
VERSION_TAG="v$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$DIR/version.py")"
ARTIFACT_BASE="TwitchDropsMinerEnhanced-$VERSION_TAG-macOS-arm64"
echo "==> Packaging $ARTIFACT_BASE.dmg installer..."
DMG_STAGE="/tmp/dmg_stage_$$"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"
cp -R "$APP_BUNDLE" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"

DMG_OUT="$DIR/$ARTIFACT_BASE.dmg"
rm -f "$DMG_OUT"
hdiutil create -volname "Twitch Drops Miner Enhanced" \
  -srcfolder "$DMG_STAGE" \
  -ov \
  -format UDZO \
  "$DMG_OUT" > /dev/null
rm -rf "$DMG_STAGE"
echo "==> Created DMG: $DMG_OUT"

# 4. Package portable ZIP
echo "==> Packaging $ARTIFACT_BASE.zip archive..."
ZIP_OUT="$DIR/$ARTIFACT_BASE.zip"
rm -f "$ZIP_OUT"
(cd "$DIR" && zip -q -r "$ZIP_OUT" "$APP_NAME.app" manual.txt README.md)
echo "==> Created ZIP: $ZIP_OUT"

echo "==> Build complete! Artifacts:"
ls -lh "$DMG_OUT" "$ZIP_OUT"
