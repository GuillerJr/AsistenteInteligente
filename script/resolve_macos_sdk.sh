#!/usr/bin/env bash
set -euo pipefail

if [[ -n "${AEGIS_MACOS_SDK:-}" ]]; then
    if [[ ! -d "$AEGIS_MACOS_SDK" ]]; then
        echo "SDK unavailable: $AEGIS_MACOS_SDK" >&2
        exit 1
    fi
    echo "$AEGIS_MACOS_SDK"
    exit 0
fi

AEGIS_ACTIVE_SDK="$(/usr/bin/xcrun --sdk macosx --show-sdk-path)"
AEGIS_SWIFTC="$(/usr/bin/xcrun --find swiftc)"
AEGIS_PLUGIN_DIRECTORY="$(/usr/bin/dirname "$AEGIS_SWIFTC")/../lib/swift/host/plugins"

sdk_is_compatible() {
    local sdk="$1"
    local interface="$sdk/System/Library/Frameworks/SwiftUICore.framework/Versions/A/Modules/SwiftUICore.swiftmodule/arm64e-apple-macos.swiftinterface"
    if [[ -f "$interface" ]] && /usr/bin/grep -q 'type: "StateMacro"' "$interface"; then
        [[ -e "$AEGIS_PLUGIN_DIRECTORY/libSwiftUIMacros.dylib" \
            || -e "$AEGIS_PLUGIN_DIRECTORY/SwiftUIMacros" ]]
    fi
}

if [[ -d "$AEGIS_ACTIVE_SDK" ]] && sdk_is_compatible "$AEGIS_ACTIVE_SDK"; then
    echo "$AEGIS_ACTIVE_SDK"
    exit 0
fi

AEGIS_SDK_DIRECTORY="$(/usr/bin/dirname "$AEGIS_ACTIVE_SDK")"
AEGIS_BEST_SDK=""
AEGIS_BEST_SCORE=-1
shopt -s nullglob
for AEGIS_CANDIDATE in "$AEGIS_SDK_DIRECTORY"/MacOSX*.sdk; do
    [[ -d "$AEGIS_CANDIDATE" && ! -L "$AEGIS_CANDIDATE" ]] || continue
    AEGIS_CANDIDATE_NAME="${AEGIS_CANDIDATE##*/}"
    AEGIS_CANDIDATE_VERSION="${AEGIS_CANDIDATE_NAME#MacOSX}"
    AEGIS_CANDIDATE_VERSION="${AEGIS_CANDIDATE_VERSION%.sdk}"
    [[ "$AEGIS_CANDIDATE_VERSION" =~ ^([0-9]+)(\.([0-9]+))?$ ]] || continue
    AEGIS_CANDIDATE_MAJOR=$((10#${BASH_REMATCH[1]}))
    AEGIS_CANDIDATE_MINOR=$((10#${BASH_REMATCH[3]:-0}))
    AEGIS_CANDIDATE_SCORE=$((AEGIS_CANDIDATE_MAJOR * 1000 + AEGIS_CANDIDATE_MINOR))
    (( AEGIS_CANDIDATE_SCORE > AEGIS_BEST_SCORE )) || continue
    if sdk_is_compatible "$AEGIS_CANDIDATE"; then
        AEGIS_BEST_SDK="$AEGIS_CANDIDATE"
        AEGIS_BEST_SCORE=$AEGIS_CANDIDATE_SCORE
    fi
done

if [[ -z "$AEGIS_BEST_SDK" ]]; then
    echo "No compatible macOS SDK found for the active Swift toolchain" >&2
    exit 1
fi

echo "$AEGIS_BEST_SDK"
