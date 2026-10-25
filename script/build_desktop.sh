#!/bin/bash
set -euo pipefail

# Development bundle only: external Python source and Codex are explicit launch flags.
JARVIS_REPO_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
JARVIS_BUILD_DIR="$(mktemp -d /private/tmp/jarvis-desktop-build.XXXXXX)"
cleanup_build_cache() {
  JARVIS_BUILD_STATUS=$?
  if [[ "$JARVIS_BUILD_STATUS" -eq 0 ]]; then
    # The staged bundle may be inside output/. Retain it, remove only this
    # invocation's reproducible compiler and package caches.
    rm -rf -- "$JARVIS_BUILD_DIR/swift" "$JARVIS_BUILD_DIR/cache" \
      "$JARVIS_BUILD_DIR/config" "$JARVIS_BUILD_DIR/security" \
      "$JARVIS_BUILD_DIR/clang-cache"
    rmdir "$JARVIS_BUILD_DIR" 2>/dev/null || true
  else
    printf 'Diagnóstico de compilación conservado en %s\n' "$JARVIS_BUILD_DIR" >&2
  fi
  exit "$JARVIS_BUILD_STATUS"
}
trap cleanup_build_cache EXIT
if [[ $# -eq 0 ]]; then
  JARVIS_OUTPUT_DIR="$JARVIS_BUILD_DIR/output"
elif [[ $# -eq 2 && "$1" == --output ]]; then
  JARVIS_OUTPUT_DIR="$2"
else
  printf '%s\n' 'Uso: build_desktop.sh [--output /private/tmp/DIRECTORIO-NUEVO]' >&2
  exit 2
fi
if [[ "$JARVIS_OUTPUT_DIR" != /private/tmp/* || "$JARVIS_OUTPUT_DIR" == *".."* || -e "$JARVIS_OUTPUT_DIR" || -L "$JARVIS_OUTPUT_DIR" ]]; then
  printf '%s\n' 'La salida debe ser una ruta nueva dentro de /private/tmp.' >&2
  exit 2
fi
JARVIS_APP_PATH="$JARVIS_OUTPUT_DIR/Jarvis.app"
export CLANG_MODULE_CACHE_PATH="$JARVIS_BUILD_DIR/clang-cache"
swift build --disable-sandbox --package-path "$JARVIS_REPO_DIR/native/JarvisDesktop" \
  --scratch-path "$JARVIS_BUILD_DIR/swift" --cache-path "$JARVIS_BUILD_DIR/cache" \
  --config-path "$JARVIS_BUILD_DIR/config" --security-path "$JARVIS_BUILD_DIR/security" \
  --arch arm64 --product Jarvis
JARVIS_BIN_DIR="$(swift build --disable-sandbox --package-path "$JARVIS_REPO_DIR/native/JarvisDesktop" \
  --scratch-path "$JARVIS_BUILD_DIR/swift" --cache-path "$JARVIS_BUILD_DIR/cache" \
  --config-path "$JARVIS_BUILD_DIR/config" --security-path "$JARVIS_BUILD_DIR/security" \
  --arch arm64 --show-bin-path)"
mkdir -p "$JARVIS_APP_PATH/Contents/MacOS"
cp "$JARVIS_BIN_DIR/Jarvis" "$JARVIS_APP_PATH/Contents/MacOS/Jarvis"
cat > "$JARVIS_APP_PATH/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.jarvis.desktop.baseline</string>
<key>CFBundleName</key><string>Jarvis</string>
<key>CFBundleDisplayName</key><string>Jarvis</string>
<key>CFBundleExecutable</key><string>Jarvis</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.1.0</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSPrincipalClass</key><string>NSApplication</string>
<key>LSUIElement</key><false/>
<key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
/usr/bin/plutil -lint "$JARVIS_APP_PATH/Contents/Info.plist"
/usr/bin/codesign --force --sign - --timestamp=none "$JARVIS_APP_PATH"
/usr/bin/codesign --verify --strict "$JARVIS_APP_PATH"
printf '%s\n' "$JARVIS_APP_PATH"
