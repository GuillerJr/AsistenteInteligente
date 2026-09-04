#!/usr/bin/env bash
set -euo pipefail

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_PACKAGE_DIR="$AEGIS_PROJECT_ROOT/native/AegisAudio"
AEGIS_SCRATCH_DIR="${AEGIS_NATIVE_TEST_ROOT:-${AEGIS_BUILD_ROOT:-/private/tmp/aegis-menubar-build}}"
AEGIS_SDK_PATH="$("$AEGIS_PROJECT_ROOT/script/resolve_macos_sdk.sh")"
AEGIS_SWIFT="$(/usr/bin/xcrun --find swift)"
AEGIS_TOOLCHAIN_ROOT="/Library/Developer/CommandLineTools"
AEGIS_FRAMEWORK_SOURCE="$AEGIS_TOOLCHAIN_ROOT/Library/Developer/Frameworks/Testing.framework"
AEGIS_INTEROP_SOURCE="$AEGIS_TOOLCHAIN_ROOT/Library/Developer/usr/lib/lib_TestingInterop.dylib"
AEGIS_MACROS="$AEGIS_TOOLCHAIN_ROOT/usr/lib/swift/host/plugins/testing/libTestingMacros.dylib"
AEGIS_FRAMEWORK_DIR="$AEGIS_SCRATCH_DIR/out/Products/Debug/PackageFrameworks"
AEGIS_FRAMEWORK_BINARY="$AEGIS_FRAMEWORK_DIR/Testing.framework/Versions/A/Testing"
AEGIS_LOCK_DIRECTORY="$AEGIS_SCRATCH_DIR.lock"

case "$AEGIS_SCRATCH_DIR" in
    /private/tmp/aegis-*)
        ;;
    *)
        echo "Native test scratch must be under /private/tmp/aegis-*" >&2
        exit 1
        ;;
esac

if ! /bin/mkdir "$AEGIS_LOCK_DIRECTORY" 2>/dev/null; then
    echo "Native tests are already running for scratch root: $AEGIS_SCRATCH_DIR" >&2
    exit 75
fi
cleanup() {
    /bin/rmdir "$AEGIS_LOCK_DIRECTORY" 2>/dev/null || true
}
trap cleanup EXIT

if [[ ! -d "$AEGIS_SDK_PATH" ]]; then
    echo "SDK unavailable: $AEGIS_SDK_PATH" >&2
    exit 1
fi
if [[ ! -x "$AEGIS_SWIFT" ]]; then
    echo "Swift unavailable: $AEGIS_SWIFT" >&2
    exit 1
fi
test -d "$AEGIS_FRAMEWORK_SOURCE"
test -f "$AEGIS_INTEROP_SOURCE"
test -f "$AEGIS_MACROS"

mkdir -p "$AEGIS_FRAMEWORK_DIR"
if [[ ! -s "$AEGIS_FRAMEWORK_BINARY" ]]; then
    rm -rf "$AEGIS_FRAMEWORK_DIR/Testing.framework"
    /bin/cp -R "$AEGIS_FRAMEWORK_SOURCE" "$AEGIS_FRAMEWORK_DIR/Testing.framework"
fi
/bin/cp -f "$AEGIS_INTEROP_SOURCE" "$AEGIS_FRAMEWORK_DIR/lib_TestingInterop.dylib"
test -s "$AEGIS_FRAMEWORK_BINARY"

env \
    AEGIS_BUILD_MLX=0 \
    SDKROOT="$AEGIS_SDK_PATH" \
    CLANG_MODULE_CACHE_PATH="$AEGIS_SCRATCH_DIR/clang-cache" \
    SWIFTPM_MODULECACHE_OVERRIDE="$AEGIS_SCRATCH_DIR/swiftpm-cache" \
    "$AEGIS_SWIFT" test \
        --package-path "$AEGIS_PACKAGE_DIR" \
        --disable-sandbox \
        --scratch-path "$AEGIS_SCRATCH_DIR" \
        -j 4 \
        -Xswiftc -F \
        -Xswiftc "$AEGIS_FRAMEWORK_DIR" \
        -Xswiftc -load-plugin-library \
        -Xswiftc "$AEGIS_MACROS" \
        -Xlinker -F \
        -Xlinker "$AEGIS_FRAMEWORK_DIR" \
        -Xlinker -rpath \
        -Xlinker "$AEGIS_FRAMEWORK_DIR"
