#!/usr/bin/env bash
set -euo pipefail

AEGIS_MODE="${1:-run}"
AEGIS_APP_NAME="AegisMenuBar"
AEGIS_BUNDLE_ID="ai.aegis.menubar"
AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_PACKAGE_DIR="$AEGIS_PROJECT_ROOT/native/AegisAudio"
AEGIS_SCRATCH_DIR="${AEGIS_BUILD_ROOT:-/private/tmp/aegis-menubar-build}"
AEGIS_SDK_PATH="${AEGIS_MACOS_SDK:-/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk}"
AEGIS_DIST_DIR="$AEGIS_PROJECT_ROOT/dist"
AEGIS_DIST_ARCHIVE="$AEGIS_DIST_DIR/$AEGIS_APP_NAME.zip"
AEGIS_APP_BUNDLE="/private/tmp/$AEGIS_APP_NAME.app"
AEGIS_APP_CONTENTS="$AEGIS_APP_BUNDLE/Contents"
AEGIS_APP_MACOS="$AEGIS_APP_CONTENTS/MacOS"
AEGIS_APP_BINARY="$AEGIS_APP_MACOS/$AEGIS_APP_NAME"
AEGIS_INFO_SOURCE="$AEGIS_PACKAGE_DIR/AppBundle/Info.plist"
AEGIS_SIGN_IDENTITY="${AEGIS_CODESIGN_IDENTITY:--}"

if [[ ! -d "$AEGIS_SDK_PATH" ]]; then
    echo "SDK unavailable: $AEGIS_SDK_PATH" >&2
    exit 1
fi

if [[ "$AEGIS_MODE" != "--package" && "$AEGIS_MODE" != "package" ]]; then
    pkill -x "$AEGIS_APP_NAME" >/dev/null 2>&1 || true
fi

env \
    SDKROOT="$AEGIS_SDK_PATH" \
    CLANG_MODULE_CACHE_PATH="$AEGIS_SCRATCH_DIR/clang-cache" \
    SWIFTPM_MODULECACHE_OVERRIDE="$AEGIS_SCRATCH_DIR/swiftpm-cache" \
    swift build \
        --package-path "$AEGIS_PACKAGE_DIR" \
        --disable-sandbox \
        --scratch-path "$AEGIS_SCRATCH_DIR" \
        --product "$AEGIS_APP_NAME"

AEGIS_BUILD_BINARY="$AEGIS_SCRATCH_DIR/out/Products/Debug/$AEGIS_APP_NAME"
test -x "$AEGIS_BUILD_BINARY"

mkdir -p "$AEGIS_DIST_DIR"
rm -rf "$AEGIS_DIST_DIR/$AEGIS_APP_NAME.app"
rm -rf "$AEGIS_APP_BUNDLE"
rm -f "$AEGIS_DIST_ARCHIVE"
mkdir -p "$AEGIS_APP_MACOS"
cp "$AEGIS_BUILD_BINARY" "$AEGIS_APP_BINARY"
cp "$AEGIS_INFO_SOURCE" "$AEGIS_APP_CONTENTS/Info.plist"
chmod +x "$AEGIS_APP_BINARY"
/usr/bin/xattr -cr "$AEGIS_APP_BUNDLE"
/usr/bin/plutil -lint "$AEGIS_APP_CONTENTS/Info.plist" >/dev/null
/usr/bin/codesign \
    --force \
    --sign "$AEGIS_SIGN_IDENTITY" \
    --timestamp=none \
    "$AEGIS_APP_BUNDLE"
/usr/bin/codesign --verify --strict "$AEGIS_APP_BUNDLE"
/usr/bin/ditto -c -k --norsrc --keepParent "$AEGIS_APP_BUNDLE" "$AEGIS_DIST_ARCHIVE"
/usr/bin/unzip -tqq "$AEGIS_DIST_ARCHIVE"

open_app() {
    /usr/bin/open -n "$AEGIS_APP_BUNDLE"
}

case "$AEGIS_MODE" in
    --package|package)
        ;;
    run)
        open_app
        ;;
    --debug|debug)
        lldb -- "$AEGIS_APP_BINARY"
        ;;
    --logs|logs)
        open_app
        /usr/bin/log stream --info --style compact --predicate "process == \"$AEGIS_APP_NAME\""
        ;;
    --telemetry|telemetry)
        open_app
        /usr/bin/log stream --info --style compact --predicate "subsystem == \"$AEGIS_BUNDLE_ID\""
        ;;
    --verify|verify)
        open_app
        sleep 1
        pgrep -x "$AEGIS_APP_NAME" >/dev/null
        ;;
    *)
        echo "usage: $0 [run|--package|--debug|--logs|--telemetry|--verify]" >&2
        exit 2
        ;;
esac
