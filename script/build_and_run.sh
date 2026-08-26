#!/usr/bin/env bash
set -euo pipefail

AEGIS_MODE="${1:-run}"
AEGIS_APP_NAME="Jarvis"
AEGIS_LEGACY_APP_NAME="AegisMenuBar"
AEGIS_BUNDLE_ID="ai.aegis.menubar"
AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_PACKAGE_DIR="$AEGIS_PROJECT_ROOT/native/AegisAudio"
AEGIS_SCRATCH_DIR="${AEGIS_BUILD_ROOT:-/private/tmp/aegis-menubar-build}"
AEGIS_SDK_PATH="$("$AEGIS_PROJECT_ROOT/script/resolve_macos_sdk.sh")"
AEGIS_SWIFT="$(/usr/bin/xcrun --find swift)"
AEGIS_DIST_DIR="$AEGIS_PROJECT_ROOT/dist"
AEGIS_DIST_ARCHIVE="$AEGIS_DIST_DIR/$AEGIS_APP_NAME.zip"
AEGIS_APP_BUNDLE="/private/tmp/$AEGIS_APP_NAME.app"
AEGIS_APP_CONTENTS="$AEGIS_APP_BUNDLE/Contents"
AEGIS_APP_MACOS="$AEGIS_APP_CONTENTS/MacOS"
AEGIS_APP_HELPERS="$AEGIS_APP_CONTENTS/Helpers"
AEGIS_APP_RESOURCES="$AEGIS_APP_CONTENTS/Resources"
AEGIS_APP_BINARY="$AEGIS_APP_MACOS/$AEGIS_APP_NAME"
AEGIS_SPEAKER_TRAINER_NAME="jarvis-speaker-trainer"
AEGIS_SPEAKER_TRAINER_BINARY="$AEGIS_APP_HELPERS/$AEGIS_SPEAKER_TRAINER_NAME"
AEGIS_LOCAL_BRAIN_NAME="jarvis-local-brain"
AEGIS_LOCAL_BRAIN_BINARY="$AEGIS_APP_HELPERS/$AEGIS_LOCAL_BRAIN_NAME"
AEGIS_COMPUTER_HELPER_PRODUCT="jarvis-computer-helper"
AEGIS_COMPUTER_HELPER_NAME="JarvisComputerHelper"
AEGIS_COMPUTER_HELPER_APP="$AEGIS_APP_HELPERS/$AEGIS_COMPUTER_HELPER_NAME.app"
AEGIS_COMPUTER_HELPER_MACOS="$AEGIS_COMPUTER_HELPER_APP/Contents/MacOS"
AEGIS_COMPUTER_HELPER_BINARY="$AEGIS_COMPUTER_HELPER_MACOS/$AEGIS_COMPUTER_HELPER_NAME"
AEGIS_INFO_SOURCE="$AEGIS_PACKAGE_DIR/AppBundle/Info.plist"
AEGIS_ENTITLEMENTS_SOURCE="$AEGIS_PACKAGE_DIR/AppBundle/Jarvis.entitlements"
AEGIS_COMPUTER_INFO_SOURCE="$AEGIS_PACKAGE_DIR/ComputerHelperBundle/Info.plist"
AEGIS_ICON_SOURCE="$AEGIS_PACKAGE_DIR/AppBundle/Resources/Jarvis.icns"
AEGIS_WAKE_MODEL_SOURCE="$HOME/Library/Application Support/Aegis/Models/JarvisWakeWord.mlmodelc"
AEGIS_SPEAKER_MODEL_SOURCE="$HOME/Library/Application Support/Aegis/Models/JarvisSpeakerIdentity.mlmodelc"
AEGIS_LOCAL_SIGN_IDENTITY_NAME="Jarvis Local Development"
AEGIS_LOCAL_SIGN_IDENTITY="$(
    /usr/bin/security find-identity -v -p codesigning 2>/dev/null \
        | /usr/bin/awk -v name="$AEGIS_LOCAL_SIGN_IDENTITY_NAME" \
            'index($0, "\"" name "\"") { print $2; exit }'
)"
if [[ -n "${AEGIS_CODESIGN_IDENTITY+x}" ]]; then
    AEGIS_SIGN_IDENTITY="$AEGIS_CODESIGN_IDENTITY"
elif [[ -n "$AEGIS_LOCAL_SIGN_IDENTITY" ]]; then
    AEGIS_SIGN_IDENTITY="$AEGIS_LOCAL_SIGN_IDENTITY"
else
    AEGIS_SIGN_IDENTITY="-"
fi
AEGIS_BUILD_CONFIGURATION="debug"
AEGIS_BUILD_DIRECTORY="Debug"
AEGIS_PREVIEW_STATE="${2:-idle}"

if [[ "$AEGIS_MODE" == "--package" || "$AEGIS_MODE" == "package" ]]; then
    AEGIS_BUILD_CONFIGURATION="release"
    AEGIS_BUILD_DIRECTORY="Release"
fi

if [[ ! -d "$AEGIS_SDK_PATH" ]]; then
    echo "SDK unavailable: $AEGIS_SDK_PATH" >&2
    exit 1
fi
if [[ ! -x "$AEGIS_SWIFT" ]]; then
    echo "Swift unavailable: $AEGIS_SWIFT" >&2
    exit 1
fi

if [[ "$AEGIS_MODE" != "--package" && "$AEGIS_MODE" != "package" ]]; then
    pkill -x "$AEGIS_APP_NAME" >/dev/null 2>&1 || true
    pkill -x "$AEGIS_LEGACY_APP_NAME" >/dev/null 2>&1 || true
fi

build_product() {
    env \
        SDKROOT="$AEGIS_SDK_PATH" \
        CLANG_MODULE_CACHE_PATH="$AEGIS_SCRATCH_DIR/clang-cache" \
        SWIFTPM_MODULECACHE_OVERRIDE="$AEGIS_SCRATCH_DIR/swiftpm-cache" \
        "$AEGIS_SWIFT" build \
            --package-path "$AEGIS_PACKAGE_DIR" \
            --configuration "$AEGIS_BUILD_CONFIGURATION" \
            --disable-sandbox \
            --scratch-path "$AEGIS_SCRATCH_DIR" \
            --product "$1"
}

build_product "$AEGIS_APP_NAME"
build_product "$AEGIS_SPEAKER_TRAINER_NAME"
build_product "$AEGIS_COMPUTER_HELPER_PRODUCT"
build_product "$AEGIS_LOCAL_BRAIN_NAME"

AEGIS_BUILD_BINARY="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_APP_NAME"
AEGIS_BUILD_SPEAKER_TRAINER="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_SPEAKER_TRAINER_NAME"
AEGIS_BUILD_COMPUTER_HELPER="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_COMPUTER_HELPER_PRODUCT"
AEGIS_BUILD_LOCAL_BRAIN="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_LOCAL_BRAIN_NAME"
test -x "$AEGIS_BUILD_BINARY"
test -x "$AEGIS_BUILD_SPEAKER_TRAINER"
test -x "$AEGIS_BUILD_COMPUTER_HELPER"
test -x "$AEGIS_BUILD_LOCAL_BRAIN"

mkdir -p "$AEGIS_DIST_DIR"
rm -rf "$AEGIS_DIST_DIR/$AEGIS_APP_NAME.app"
rm -rf "$AEGIS_APP_BUNDLE"
rm -f "$AEGIS_DIST_ARCHIVE"
mkdir -p \
    "$AEGIS_APP_MACOS" \
    "$AEGIS_APP_HELPERS" \
    "$AEGIS_APP_RESOURCES" \
    "$AEGIS_COMPUTER_HELPER_MACOS"
cp "$AEGIS_BUILD_BINARY" "$AEGIS_APP_BINARY"
cp "$AEGIS_BUILD_SPEAKER_TRAINER" "$AEGIS_SPEAKER_TRAINER_BINARY"
cp "$AEGIS_BUILD_COMPUTER_HELPER" "$AEGIS_COMPUTER_HELPER_BINARY"
cp "$AEGIS_BUILD_LOCAL_BRAIN" "$AEGIS_LOCAL_BRAIN_BINARY"
cp "$AEGIS_INFO_SOURCE" "$AEGIS_APP_CONTENTS/Info.plist"
cp "$AEGIS_COMPUTER_INFO_SOURCE" "$AEGIS_COMPUTER_HELPER_APP/Contents/Info.plist"
cp "$AEGIS_ICON_SOURCE" "$AEGIS_APP_RESOURCES/Jarvis.icns"
if [[ -e "$AEGIS_WAKE_MODEL_SOURCE" ]]; then
    if [[ -L "$AEGIS_WAKE_MODEL_SOURCE" || ! -d "$AEGIS_WAKE_MODEL_SOURCE" ]]; then
        echo "Invalid wake word model asset" >&2
        exit 1
    fi
    /usr/bin/ditto --norsrc "$AEGIS_WAKE_MODEL_SOURCE" \
        "$AEGIS_APP_RESOURCES/JarvisWakeWord.mlmodelc"
fi
if [[ -e "$AEGIS_SPEAKER_MODEL_SOURCE" ]]; then
    if [[ -L "$AEGIS_SPEAKER_MODEL_SOURCE" || ! -d "$AEGIS_SPEAKER_MODEL_SOURCE" ]]; then
        echo "Invalid speaker identity model asset" >&2
        exit 1
    fi
    /usr/bin/ditto --norsrc "$AEGIS_SPEAKER_MODEL_SOURCE" \
        "$AEGIS_APP_RESOURCES/JarvisSpeakerIdentity.mlmodelc"
fi
chmod +x \
    "$AEGIS_APP_BINARY" \
    "$AEGIS_SPEAKER_TRAINER_BINARY" \
    "$AEGIS_LOCAL_BRAIN_BINARY" \
    "$AEGIS_COMPUTER_HELPER_BINARY"
/usr/bin/xattr -cr "$AEGIS_APP_BUNDLE"
/usr/bin/plutil -lint "$AEGIS_APP_CONTENTS/Info.plist" >/dev/null
/usr/bin/plutil -lint "$AEGIS_ENTITLEMENTS_SOURCE" >/dev/null
/usr/bin/plutil -lint "$AEGIS_COMPUTER_HELPER_APP/Contents/Info.plist" >/dev/null
if [[ "$AEGIS_SIGN_IDENTITY" == "-" ]]; then
    /usr/bin/codesign --force --sign - --timestamp=none "$AEGIS_SPEAKER_TRAINER_BINARY"
    /usr/bin/codesign --force --sign - --timestamp=none "$AEGIS_LOCAL_BRAIN_BINARY"
    /usr/bin/codesign --force --sign - --timestamp=none "$AEGIS_COMPUTER_HELPER_APP"
    /usr/bin/codesign \
        --force \
        --sign - \
        --entitlements "$AEGIS_ENTITLEMENTS_SOURCE" \
        --timestamp=none \
        "$AEGIS_APP_BUNDLE"
else
    AEGIS_TIMESTAMP_ARGUMENT="--timestamp"
    if [[
        "$AEGIS_SIGN_IDENTITY" == "$AEGIS_LOCAL_SIGN_IDENTITY"
        || "$AEGIS_SIGN_IDENTITY" == "$AEGIS_LOCAL_SIGN_IDENTITY_NAME"
    ]]; then
        AEGIS_TIMESTAMP_ARGUMENT="--timestamp=none"
    fi
    /usr/bin/codesign \
        --force \
        --sign "$AEGIS_SIGN_IDENTITY" \
        --options runtime \
        "$AEGIS_TIMESTAMP_ARGUMENT" \
        "$AEGIS_SPEAKER_TRAINER_BINARY"
    /usr/bin/codesign \
        --force \
        --sign "$AEGIS_SIGN_IDENTITY" \
        --options runtime \
        "$AEGIS_TIMESTAMP_ARGUMENT" \
        "$AEGIS_LOCAL_BRAIN_BINARY"
    /usr/bin/codesign \
        --force \
        --sign "$AEGIS_SIGN_IDENTITY" \
        --options runtime \
        "$AEGIS_TIMESTAMP_ARGUMENT" \
        "$AEGIS_COMPUTER_HELPER_APP"
    /usr/bin/codesign \
        --force \
        --sign "$AEGIS_SIGN_IDENTITY" \
        --entitlements "$AEGIS_ENTITLEMENTS_SOURCE" \
        --options runtime \
        "$AEGIS_TIMESTAMP_ARGUMENT" \
        "$AEGIS_APP_BUNDLE"
fi
/usr/bin/codesign --verify --deep --strict "$AEGIS_APP_BUNDLE"
AEGIS_SIGNED_ENTITLEMENTS="$(
    /usr/bin/codesign -d --entitlements :- "$AEGIS_APP_BUNDLE" 2>/dev/null
)"
if [[
    "$AEGIS_SIGNED_ENTITLEMENTS" != *"com.apple.security.device.audio-input"*
    || "$AEGIS_SIGNED_ENTITLEMENTS" != *"com.apple.security.automation.apple-events"*
]]; then
    echo "Required Jarvis entitlements are missing from the signed bundle" >&2
    exit 1
fi
/usr/bin/ditto -c -k --norsrc --keepParent "$AEGIS_APP_BUNDLE" "$AEGIS_DIST_ARCHIVE"
/usr/bin/unzip -tqq "$AEGIS_DIST_ARCHIVE"
/bin/rm -rf "$AEGIS_DIST_DIR/$AEGIS_LEGACY_APP_NAME.app" "/private/tmp/$AEGIS_LEGACY_APP_NAME.app"
/bin/rm -f "$AEGIS_DIST_DIR/$AEGIS_LEGACY_APP_NAME.zip"

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
    --notch-preview|notch-preview)
        case "$AEGIS_PREVIEW_STATE" in
            idle|ambient|listening|processing|approval|speaking|failure|cycle)
                ;;
            *)
                echo "invalid notch preview state: $AEGIS_PREVIEW_STATE" >&2
                exit 2
                ;;
        esac
        /usr/bin/open -n "$AEGIS_APP_BUNDLE" --args "--notch-preview=$AEGIS_PREVIEW_STATE"
        ;;
    *)
        echo "usage: $0 [run|--package|--debug|--logs|--telemetry|--verify|--notch-preview STATE]" >&2
        exit 2
        ;;
esac
