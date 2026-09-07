#!/usr/bin/env bash
set -euo pipefail

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
AEGIS_INSTALLED_INFO="$HOME/Applications/Jarvis.app/Contents/Info.plist"
AEGIS_FIXTURE_ROOT=""
AEGIS_FIXTURE_PID=""
AEGIS_BUILD_ROOT="${AEGIS_P8_BUILD_ROOT:-/private/tmp/aegis-p8-qualification-build}"
AEGIS_BUILD_LOCK="$AEGIS_BUILD_ROOT.lock"
AEGIS_BUILD_LOCK_HELD=0

cleanup() {
    if [[ "$AEGIS_FIXTURE_PID" =~ ^[1-9][0-9]*$ ]]; then
        /bin/kill -TERM "$AEGIS_FIXTURE_PID" 2>/dev/null || true
        for _ in {1..20}; do
            /bin/kill -0 "$AEGIS_FIXTURE_PID" 2>/dev/null || break
            sleep 0.05
        done
    fi
    case "$AEGIS_FIXTURE_ROOT" in
        /private/tmp/aegis-p8.*)
            /bin/rm -rf -- "$AEGIS_FIXTURE_ROOT"
            ;;
    esac
    if [[ "$AEGIS_BUILD_LOCK_HELD" == "1" ]]; then
        /bin/rmdir "$AEGIS_BUILD_LOCK" 2>/dev/null || true
    fi
}
trap cleanup EXIT INT TERM HUP

cd "$AEGIS_PROJECT_ROOT"
if [[ -L "$HOME/Applications/Jarvis.app" || ! -f "$AEGIS_INSTALLED_INFO" ]]; then
    echo "status=blocked reason=current_app_not_installed" >&2
    exit 2
fi
AEGIS_SOURCE_REVISION="$(git rev-parse HEAD)"
AEGIS_APP_REVISION="$(
    /usr/bin/plutil -extract AegisBuildRevision raw "$AEGIS_INSTALLED_INFO" 2>/dev/null || true
)"
if [[ "$AEGIS_APP_REVISION" != "$AEGIS_SOURCE_REVISION" ]]; then
    echo "status=blocked reason=installed_app_revision_mismatch" >&2
    exit 2
fi
if /usr/bin/pgrep -x JarvisP8Fixture >/dev/null 2>&1; then
    echo "status=blocked reason=qualification_fixture_already_running" >&2
    exit 2
fi
case "$AEGIS_BUILD_ROOT" in
    /private/tmp/aegis-p8-*)
        ;;
    *)
        echo "status=error reason=qualification_build_root_invalid" >&2
        exit 2
        ;;
esac
if ! /bin/mkdir "$AEGIS_BUILD_LOCK" 2>/dev/null; then
    echo "status=blocked reason=qualification_build_already_running" >&2
    exit 75
fi
AEGIS_BUILD_LOCK_HELD=1

echo "[aegis-p8] Revalidating the P7 installed-runtime boundary"
./script/p7_reliability_gate.sh

echo "[aegis-p8] Building the disposable native AppKit qualification fixture"
AEGIS_SWIFTC="$(/usr/bin/xcrun --find swiftc)"
AEGIS_SDK_PATH="$("$AEGIS_PROJECT_ROOT/script/resolve_macos_sdk.sh")"
/bin/mkdir -p "$AEGIS_BUILD_ROOT/clang-cache" "$AEGIS_BUILD_ROOT/swift-cache"
AEGIS_FIXTURE_BINARY="$AEGIS_BUILD_ROOT/JarvisP8Fixture"
env \
    SDKROOT="$AEGIS_SDK_PATH" \
    CLANG_MODULE_CACHE_PATH="$AEGIS_BUILD_ROOT/clang-cache" \
    SWIFT_MODULE_CACHE_PATH="$AEGIS_BUILD_ROOT/swift-cache" \
    "$AEGIS_SWIFTC" \
        -parse-as-library \
        -O \
        -whole-module-optimization \
        -sdk "$AEGIS_SDK_PATH" \
        -target "arm64-apple-macos14.0" \
        -framework AppKit \
        "$AEGIS_PROJECT_ROOT/native/AegisAudio/Sources/JarvisUIQualificationFixture/main.swift" \
        -o "$AEGIS_FIXTURE_BINARY"
if [[ -L "$AEGIS_FIXTURE_BINARY" || ! -x "$AEGIS_FIXTURE_BINARY" ]]; then
    echo "status=error reason=qualification_fixture_build_missing" >&2
    exit 1
fi

AEGIS_FIXTURE_ROOT="$(mktemp -d /private/tmp/aegis-p8.XXXXXX)"
AEGIS_FIXTURE_APP="$AEGIS_FIXTURE_ROOT/JarvisP8Fixture.app"
/bin/mkdir -p "$AEGIS_FIXTURE_APP/Contents/MacOS"
/usr/bin/ditto "$AEGIS_FIXTURE_BINARY" \
    "$AEGIS_FIXTURE_APP/Contents/MacOS/JarvisP8Fixture"
/usr/bin/ditto "$AEGIS_PROJECT_ROOT/tests/fixtures/JarvisP8Fixture-Info.plist" \
    "$AEGIS_FIXTURE_APP/Contents/Info.plist"
/bin/chmod 755 "$AEGIS_FIXTURE_APP/Contents/MacOS/JarvisP8Fixture"
/usr/bin/plutil -lint "$AEGIS_FIXTURE_APP/Contents/Info.plist" >/dev/null
/usr/bin/codesign --force --sign - "$AEGIS_FIXTURE_APP" >/dev/null
/usr/bin/codesign --verify --strict "$AEGIS_FIXTURE_APP"
/usr/bin/open -g -n "$AEGIS_FIXTURE_APP"

for _ in {1..50}; do
    AEGIS_FIXTURE_PID="$(/usr/bin/pgrep -x JarvisP8Fixture 2>/dev/null || true)"
    [[ "$AEGIS_FIXTURE_PID" =~ ^[1-9][0-9]*$ ]] && break
    sleep 0.1
done
if [[ ! "$AEGIS_FIXTURE_PID" =~ ^[1-9][0-9]*$ ]]; then
    echo "status=error reason=qualification_fixture_launch_failed" >&2
    exit 1
fi

echo "[aegis-p8] Exercising capture, AX text, AX press, focus and pointer isolation"
./script/aegis.sh application-qualification
echo "status=ok block=P8 owner_voice=deferred fixture=ephemeral"
