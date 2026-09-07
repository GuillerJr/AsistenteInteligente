#!/usr/bin/env bash
set -euo pipefail

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
AEGIS_INSTALLED_INFO="$HOME/Applications/Jarvis.app/Contents/Info.plist"
AEGIS_CHROME_APP="/Applications/Google Chrome.app"
AEGIS_CHROME_BINARY="$AEGIS_CHROME_APP/Contents/MacOS/Google Chrome"
AEGIS_CHROME_INFO="$AEGIS_CHROME_APP/Contents/Info.plist"
AEGIS_CHROME_PID=""
AEGIS_FIXTURE_ROOT=""

cleanup() {
    if [[ "$AEGIS_CHROME_PID" =~ ^[1-9][0-9]*$ ]]; then
        /bin/kill -TERM "$AEGIS_CHROME_PID" 2>/dev/null || true
        for _ in {1..40}; do
            /bin/kill -0 "$AEGIS_CHROME_PID" 2>/dev/null || break
            sleep 0.05
        done
        if /bin/kill -0 "$AEGIS_CHROME_PID" 2>/dev/null; then
            /bin/kill -KILL "$AEGIS_CHROME_PID" 2>/dev/null || true
        fi
    fi
    case "$AEGIS_FIXTURE_ROOT" in
        /private/tmp/aegis-p9.*)
            /bin/rm -rf -- "$AEGIS_FIXTURE_ROOT"
            ;;
    esac
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
if [[ -L "$AEGIS_CHROME_APP" || ! -x "$AEGIS_CHROME_BINARY" || ! -f "$AEGIS_CHROME_INFO" ]]; then
    echo "status=blocked reason=google_chrome_not_installed" >&2
    exit 2
fi
if [[ "$(/usr/bin/plutil -extract CFBundleIdentifier raw "$AEGIS_CHROME_INFO")" \
    != "com.google.Chrome" ]]; then
    echo "status=blocked reason=chrome_bundle_identity_invalid" >&2
    exit 2
fi
/usr/bin/codesign --verify --deep --strict "$AEGIS_CHROME_APP"
if /usr/sbin/lsof -nP -iTCP:9222 -sTCP:LISTEN >/dev/null 2>&1; then
    echo "status=blocked reason=chrome_debugging_port_in_use" >&2
    exit 2
fi

echo "[aegis-p9] Revalidating the P8 native application boundary"
./script/p8_application_gate.sh

AEGIS_FIXTURE_ROOT="$(mktemp -d /private/tmp/aegis-p9.XXXXXX)"
AEGIS_PROFILE="$AEGIS_FIXTURE_ROOT/ChromeProfile"
/bin/mkdir -p "$AEGIS_PROFILE"

echo "[aegis-p9] Starting signed Chrome with an isolated ephemeral profile"
"$AEGIS_CHROME_BINARY" \
    --headless=new \
    --remote-debugging-address=127.0.0.1 \
    --remote-debugging-port=9222 \
    --user-data-dir="$AEGIS_PROFILE" \
    --proxy-server=127.0.0.1:9 \
    --proxy-bypass-list="<-loopback>" \
    --disable-background-networking \
    --disable-breakpad \
    --disable-client-side-phishing-detection \
    --disable-component-extensions-with-background-pages \
    --disable-component-update \
    --disable-crash-reporter \
    --disable-default-apps \
    --disable-domain-reliability \
    --disable-features=AutofillServerCommunication,MediaRouter,OptimizationHints \
    --disable-sync \
    --metrics-recording-only \
    --no-default-browser-check \
    --no-first-run \
    --password-store=basic \
    --use-mock-keychain \
    about:blank \
    >/dev/null 2>&1 &
AEGIS_CHROME_PID="$!"

AEGIS_CDP_READY=0
for _ in {1..80}; do
    if ! /bin/kill -0 "$AEGIS_CHROME_PID" 2>/dev/null; then
        break
    fi
    if /usr/bin/curl \
        --fail \
        --silent \
        --show-error \
        --max-time 0.25 \
        http://127.0.0.1:9222/json/version \
        >/dev/null 2>&1; then
        AEGIS_CDP_READY=1
        break
    fi
    sleep 0.05
done
if [[ "$AEGIS_CDP_READY" != "1" ]]; then
    echo "status=error reason=isolated_chrome_launch_failed" >&2
    exit 1
fi

echo "[aegis-p9] Exercising loopback CDP, in-memory DOM and owner-input isolation"
./script/aegis.sh browser-driver-qualification
echo "status=ok block=P9 browser=chrome profile=ephemeral owner_voice=deferred"
