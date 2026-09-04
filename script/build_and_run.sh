#!/usr/bin/env bash
set -euo pipefail

AEGIS_MODE="${1:-run}"
AEGIS_APP_NAME="Jarvis"
AEGIS_LEGACY_APP_NAME="AegisMenuBar"
AEGIS_BUNDLE_ID="ai.aegis.menubar"
AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_BUILD_REVISION="$(/usr/bin/git -C "$AEGIS_PROJECT_ROOT" rev-parse --verify HEAD)"
AEGIS_BUILD_DIRTY=false
if ! /usr/bin/git -C "$AEGIS_PROJECT_ROOT" diff --quiet --ignore-submodules -- \
    || ! /usr/bin/git -C "$AEGIS_PROJECT_ROOT" diff --cached --quiet --ignore-submodules --
then
    AEGIS_BUILD_DIRTY=true
fi
if [[ ! "$AEGIS_BUILD_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
    echo "Invalid Git build revision" >&2
    exit 1
fi
AEGIS_PACKAGE_DIR="$AEGIS_PROJECT_ROOT/native/AegisAudio"
AEGIS_SCRATCH_DIR="${AEGIS_BUILD_ROOT:-/private/tmp/aegis-menubar-build}"
AEGIS_SDK_PATH="$("$AEGIS_PROJECT_ROOT/script/resolve_macos_sdk.sh")"
AEGIS_SWIFT="$(/usr/bin/xcrun --find swift)"
AEGIS_SWIFTC="$(/usr/bin/xcrun --find swiftc)"
AEGIS_SWIFT_PLUGIN_DIRECTORY="$(/usr/bin/dirname "$AEGIS_SWIFTC")/../lib/swift/host/plugins"
AEGIS_FOUNDATION_MODELS_ENABLED=0
AEGIS_FOUNDATION_MODELS_VISION_ENABLED=0
if [[
    -e "$AEGIS_SWIFT_PLUGIN_DIRECTORY/libFoundationModelsMacros.dylib"
    || -e "$AEGIS_SWIFT_PLUGIN_DIRECTORY/FoundationModelsMacros"
]]; then
    AEGIS_FOUNDATION_MODELS_ENABLED=1
fi
AEGIS_SDK_NAME="${AEGIS_SDK_PATH##*/}"
if [[
    "$AEGIS_FOUNDATION_MODELS_ENABLED" == "1"
    && "$AEGIS_SDK_NAME" =~ ^MacOSX(2[7-9]|[3-9][0-9])
]]; then
    AEGIS_FOUNDATION_MODELS_VISION_ENABLED=1
fi
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
AEGIS_BIOMETRIC_CALIBRATOR_NAME="jarvis-biometric-calibrator"
AEGIS_BIOMETRIC_CALIBRATOR_BINARY="$AEGIS_APP_HELPERS/$AEGIS_BIOMETRIC_CALIBRATOR_NAME"
AEGIS_LOCAL_BRAIN_NAME="jarvis-local-brain"
AEGIS_LOCAL_BRAIN_BINARY="$AEGIS_APP_HELPERS/$AEGIS_LOCAL_BRAIN_NAME"
AEGIS_LOCAL_EMBEDDING_NAME="jarvis-local-embedding"
AEGIS_LOCAL_EMBEDDING_BINARY="$AEGIS_APP_HELPERS/$AEGIS_LOCAL_EMBEDDING_NAME"
AEGIS_IOS_BRIDGE_NAME="jarvis-ios-bridge"
AEGIS_IOS_BRIDGE_BINARY="$AEGIS_APP_HELPERS/$AEGIS_IOS_BRIDGE_NAME"
AEGIS_MLX_ENGINE_NAME="jarvis-mlx-engine"
AEGIS_MLX_ENGINE_BINARY="$AEGIS_APP_HELPERS/$AEGIS_MLX_ENGINE_NAME"
AEGIS_CORE_RESOURCE_BUNDLE_NAME="AegisAudio_AegisAudioCore.bundle"
AEGIS_MLX_RESOURCE_BUNDLES=(
    "mlx-swift_Cmlx.bundle"
    "swift-crypto_Crypto.bundle"
    "swift-transformers_Hub.bundle"
)
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
AEGIS_BUILD_MLX="${AEGIS_BUILD_MLX:-auto}"

if [[ "$AEGIS_MODE" == "--package" || "$AEGIS_MODE" == "package" ]]; then
    AEGIS_BUILD_CONFIGURATION="release"
    AEGIS_BUILD_DIRECTORY="Release"
fi
AEGIS_CORE_RESOURCE_BUNDLE_SOURCE="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_CORE_RESOURCE_BUNDLE_NAME"
if [[ "$AEGIS_BUILD_MLX" == "auto" ]]; then
    if /usr/bin/xcrun --find metal >/dev/null 2>&1; then
        AEGIS_BUILD_MLX="1"
    else
        AEGIS_BUILD_MLX="0"
    fi
fi
if [[ "$AEGIS_BUILD_MLX" != "0" && "$AEGIS_BUILD_MLX" != "1" ]]; then
    echo "AEGIS_BUILD_MLX must be 0, 1, or auto" >&2
    exit 2
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
    if [[ "$AEGIS_FOUNDATION_MODELS_ENABLED" == "1" ]]; then
        AEGIS_FOUNDATION_SWIFT_FLAGS=(-Xswiftc -DAEGIS_FOUNDATION_MODELS_MACROS)
        if [[ "$AEGIS_FOUNDATION_MODELS_VISION_ENABLED" == "1" ]]; then
            AEGIS_FOUNDATION_SWIFT_FLAGS+=(
                -Xswiftc -DAEGIS_FOUNDATION_MODELS_VISION
            )
        fi
        env \
            AEGIS_BUILD_MLX="$AEGIS_BUILD_MLX" \
            SDKROOT="$AEGIS_SDK_PATH" \
            CLANG_MODULE_CACHE_PATH="$AEGIS_SCRATCH_DIR/clang-cache" \
            SWIFTPM_MODULECACHE_OVERRIDE="$AEGIS_SCRATCH_DIR/swiftpm-cache" \
            "$AEGIS_SWIFT" build \
                --package-path "$AEGIS_PACKAGE_DIR" \
                --configuration "$AEGIS_BUILD_CONFIGURATION" \
                --disable-sandbox \
                --scratch-path "$AEGIS_SCRATCH_DIR" \
                "${AEGIS_FOUNDATION_SWIFT_FLAGS[@]}" \
                --product "$1"
        return
    fi
    env \
        AEGIS_BUILD_MLX="$AEGIS_BUILD_MLX" \
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
build_product "$AEGIS_BIOMETRIC_CALIBRATOR_NAME"
build_product "$AEGIS_COMPUTER_HELPER_PRODUCT"
build_product "$AEGIS_LOCAL_BRAIN_NAME"
build_product "$AEGIS_LOCAL_EMBEDDING_NAME"
build_product "$AEGIS_IOS_BRIDGE_NAME"
if [[ "$AEGIS_BUILD_MLX" == "1" ]]; then
    if ! /usr/bin/xcrun --find metal >/dev/null 2>&1; then
        echo "The MLX helper requires the full Xcode Metal toolchain" >&2
        exit 1
    fi
    build_product "$AEGIS_MLX_ENGINE_NAME"
fi

AEGIS_BUILD_BINARY="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_APP_NAME"
AEGIS_BUILD_SPEAKER_TRAINER="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_SPEAKER_TRAINER_NAME"
AEGIS_BUILD_BIOMETRIC_CALIBRATOR="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_BIOMETRIC_CALIBRATOR_NAME"
AEGIS_BUILD_COMPUTER_HELPER="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_COMPUTER_HELPER_PRODUCT"
AEGIS_BUILD_LOCAL_BRAIN="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_LOCAL_BRAIN_NAME"
AEGIS_BUILD_LOCAL_EMBEDDING="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_LOCAL_EMBEDDING_NAME"
AEGIS_BUILD_IOS_BRIDGE="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_IOS_BRIDGE_NAME"
AEGIS_BUILD_MLX_ENGINE="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$AEGIS_MLX_ENGINE_NAME"
test -x "$AEGIS_BUILD_BINARY"
test -x "$AEGIS_BUILD_SPEAKER_TRAINER"
test -x "$AEGIS_BUILD_BIOMETRIC_CALIBRATOR"
test -x "$AEGIS_BUILD_COMPUTER_HELPER"
test -x "$AEGIS_BUILD_LOCAL_BRAIN"
test -x "$AEGIS_BUILD_LOCAL_EMBEDDING"
test -x "$AEGIS_BUILD_IOS_BRIDGE"
if [[ "$AEGIS_BUILD_MLX" == "1" ]]; then
    test -x "$AEGIS_BUILD_MLX_ENGINE"
fi

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
cp "$AEGIS_BUILD_BIOMETRIC_CALIBRATOR" "$AEGIS_BIOMETRIC_CALIBRATOR_BINARY"
cp "$AEGIS_BUILD_COMPUTER_HELPER" "$AEGIS_COMPUTER_HELPER_BINARY"
cp "$AEGIS_BUILD_LOCAL_BRAIN" "$AEGIS_LOCAL_BRAIN_BINARY"
cp "$AEGIS_BUILD_LOCAL_EMBEDDING" "$AEGIS_LOCAL_EMBEDDING_BINARY"
cp "$AEGIS_BUILD_IOS_BRIDGE" "$AEGIS_IOS_BRIDGE_BINARY"
if [[ "$AEGIS_BUILD_MLX" == "1" ]]; then
    cp "$AEGIS_BUILD_MLX_ENGINE" "$AEGIS_MLX_ENGINE_BINARY"
    for bundle_name in "${AEGIS_MLX_RESOURCE_BUNDLES[@]}"; do
        bundle_source="$AEGIS_SCRATCH_DIR/out/Products/$AEGIS_BUILD_DIRECTORY/$bundle_name"
        test -d "$bundle_source"
        /usr/bin/ditto "$bundle_source" "$AEGIS_APP_RESOURCES/$bundle_name"
    done
fi
cp "$AEGIS_INFO_SOURCE" "$AEGIS_APP_CONTENTS/Info.plist"
/usr/bin/plutil -insert AegisBuildRevision -string "$AEGIS_BUILD_REVISION" \
    "$AEGIS_APP_CONTENTS/Info.plist"
/usr/bin/plutil -insert AegisBuildDirty -bool "$AEGIS_BUILD_DIRTY" \
    "$AEGIS_APP_CONTENTS/Info.plist"
cp "$AEGIS_COMPUTER_INFO_SOURCE" "$AEGIS_COMPUTER_HELPER_APP/Contents/Info.plist"
cp "$AEGIS_ICON_SOURCE" "$AEGIS_APP_RESOURCES/Jarvis.icns"
test -d "$AEGIS_CORE_RESOURCE_BUNDLE_SOURCE"
/usr/bin/ditto --norsrc "$AEGIS_CORE_RESOURCE_BUNDLE_SOURCE" \
    "$AEGIS_APP_RESOURCES/$AEGIS_CORE_RESOURCE_BUNDLE_NAME"
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
    "$AEGIS_BIOMETRIC_CALIBRATOR_BINARY" \
    "$AEGIS_LOCAL_BRAIN_BINARY" \
    "$AEGIS_LOCAL_EMBEDDING_BINARY" \
    "$AEGIS_IOS_BRIDGE_BINARY" \
    "$AEGIS_COMPUTER_HELPER_BINARY"
if [[ "$AEGIS_BUILD_MLX" == "1" ]]; then
    chmod +x "$AEGIS_MLX_ENGINE_BINARY"
fi
/usr/bin/xattr -cr "$AEGIS_APP_BUNDLE"
/usr/bin/plutil -lint "$AEGIS_APP_CONTENTS/Info.plist" >/dev/null
/usr/bin/plutil -lint "$AEGIS_ENTITLEMENTS_SOURCE" >/dev/null
/usr/bin/plutil -lint "$AEGIS_COMPUTER_HELPER_APP/Contents/Info.plist" >/dev/null
if [[ "$AEGIS_SIGN_IDENTITY" == "-" ]]; then
    /usr/bin/codesign --force --sign - --timestamp=none "$AEGIS_SPEAKER_TRAINER_BINARY"
    /usr/bin/codesign --force --sign - --timestamp=none "$AEGIS_BIOMETRIC_CALIBRATOR_BINARY"
    /usr/bin/codesign --force --sign - --timestamp=none "$AEGIS_LOCAL_BRAIN_BINARY"
    /usr/bin/codesign --force --sign - --timestamp=none "$AEGIS_LOCAL_EMBEDDING_BINARY"
    /usr/bin/codesign --force --sign - --timestamp=none "$AEGIS_IOS_BRIDGE_BINARY"
    if [[ "$AEGIS_BUILD_MLX" == "1" ]]; then
        /usr/bin/codesign --force --deep --sign - --timestamp=none "$AEGIS_MLX_ENGINE_BINARY"
    fi
    /usr/bin/codesign --force --deep --sign - --timestamp=none "$AEGIS_COMPUTER_HELPER_APP"
    /usr/bin/codesign \
        --force \
        --deep \
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
        --deep \
        --sign "$AEGIS_SIGN_IDENTITY" \
        --options runtime \
        "$AEGIS_TIMESTAMP_ARGUMENT" \
        "$AEGIS_SPEAKER_TRAINER_BINARY"
    /usr/bin/codesign \
        --force \
        --deep \
        --sign "$AEGIS_SIGN_IDENTITY" \
        --options runtime \
        "$AEGIS_TIMESTAMP_ARGUMENT" \
        "$AEGIS_BIOMETRIC_CALIBRATOR_BINARY"
    /usr/bin/codesign \
        --force \
        --deep \
        --sign "$AEGIS_SIGN_IDENTITY" \
        --options runtime \
        "$AEGIS_TIMESTAMP_ARGUMENT" \
        "$AEGIS_LOCAL_BRAIN_BINARY"
    /usr/bin/codesign \
        --force \
        --deep \
        --sign "$AEGIS_SIGN_IDENTITY" \
        --options runtime \
        "$AEGIS_TIMESTAMP_ARGUMENT" \
        "$AEGIS_LOCAL_EMBEDDING_BINARY"
    /usr/bin/codesign \
        --force \
        --deep \
        --sign "$AEGIS_SIGN_IDENTITY" \
        --options runtime \
        "$AEGIS_TIMESTAMP_ARGUMENT" \
        "$AEGIS_IOS_BRIDGE_BINARY"
    if [[ "$AEGIS_BUILD_MLX" == "1" ]]; then
        /usr/bin/codesign \
            --force \
            --deep \
            --sign "$AEGIS_SIGN_IDENTITY" \
            --options runtime \
            "$AEGIS_TIMESTAMP_ARGUMENT" \
            "$AEGIS_MLX_ENGINE_BINARY"
    fi
    /usr/bin/codesign \
        --force \
        --deep \
        --sign "$AEGIS_SIGN_IDENTITY" \
        --options runtime \
        "$AEGIS_TIMESTAMP_ARGUMENT" \
        "$AEGIS_COMPUTER_HELPER_APP"
    /usr/bin/codesign \
        --force \
        --deep \
        --sign "$AEGIS_SIGN_IDENTITY" \
        --entitlements "$AEGIS_ENTITLEMENTS_SOURCE" \
        --options runtime \
        "$AEGIS_TIMESTAMP_ARGUMENT" \
        "$AEGIS_APP_BUNDLE"
fi
/usr/bin/codesign --verify --deep --strict "$AEGIS_APP_BUNDLE"
AEGIS_SIGNED_ENTITLEMENTS="$(
    /usr/bin/codesign -d --entitlements - "$AEGIS_APP_BUNDLE" 2>&1
)"
if [[
    "$AEGIS_SIGNED_ENTITLEMENTS" != *"com.apple.security.device.audio-input"*
    || "$AEGIS_SIGNED_ENTITLEMENTS" != *"com.apple.security.device.microphone"*
    || "$AEGIS_SIGNED_ENTITLEMENTS" != *"com.apple.security.automation.apple-events"*
    || "$AEGIS_SIGNED_ENTITLEMENTS" != *"com.apple.security.personal-information.addressbook"*
    || "$AEGIS_SIGNED_ENTITLEMENTS" != *"com.apple.security.personal-information.calendars"*
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
