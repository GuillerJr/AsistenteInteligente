#!/usr/bin/env bash
set -euo pipefail

AEGIS_ACTION="${1:-daily}"
AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_SOURCE_REVISION="$(/usr/bin/git -C "$AEGIS_PROJECT_ROOT" rev-parse HEAD 2>/dev/null || true)"
AEGIS_APP="$HOME/Applications/Jarvis.app"
AEGIS_APP_BINARY="$AEGIS_APP/Contents/MacOS/Jarvis"
AEGIS_APP_INFO="$AEGIS_APP/Contents/Info.plist"
AEGIS_READINESS="$HOME/Library/Application Support/Aegis/runtime-readiness.json"
AEGIS_SOCKET="$HOME/Library/Application Support/Aegis/aegis.sock"
AEGIS_PYTHON="$AEGIS_PROJECT_ROOT/.venv/bin/python"
AEGIS_TEMPORARY=""
AEGIS_FAILURES=0

cleanup() {
    if [[ -n "$AEGIS_TEMPORARY" ]]; then
        /bin/rm -f "$AEGIS_TEMPORARY"
    fi
}

trap cleanup EXIT

pass() {
    echo "[ok] $1"
}

fail() {
    echo "[error] $1${2:+: $2}" >&2
    AEGIS_FAILURES=$((AEGIS_FAILURES + 1))
}

run_check() {
    local label="$1"
    shift
    local output
    if output="$("$@" 2>&1)"; then
        pass "$label"
    else
        fail "$label" "$output"
    fi
}

check_private_path() {
    local label="$1"
    local path="$2"
    local kind="$3"
    local mode owner
    if [[ "$kind" == "socket" ]]; then
        if [[ ! -S "$path" ]]; then
            fail "$label" "missing_socket"
            return
        fi
    elif [[ ! -f "$path" || -L "$path" ]]; then
        fail "$label" "missing_or_unsafe_file"
        return
    fi
    mode="$(/usr/bin/stat -f '%Lp' "$path")"
    owner="$(/usr/bin/stat -f '%u' "$path")"
    if [[ "$mode" != "600" || "$owner" != "$(id -u)" ]]; then
        fail "$label" "owner=$owner mode=$mode expected_owner=$(id -u) expected_mode=600"
        return
    fi
    pass "$label"
}

check_entitlements() {
    AEGIS_TEMPORARY="$(/usr/bin/mktemp /private/tmp/jarvis-entitlements.XXXXXX)"
    if ! /usr/bin/codesign -d --entitlements "$AEGIS_TEMPORARY" "$AEGIS_APP" \
        >/dev/null 2>&1
    then
        fail "Entitlements firmados" "codesign_read_failed"
        return
    fi
    local entitlement
    for entitlement in \
        com.apple.security.device.microphone \
        com.apple.security.automation.apple-events \
        com.apple.security.personal-information.addressbook \
        com.apple.security.personal-information.calendars
    do
        if ! /usr/bin/awk -v entitlement="$entitlement" '
            $0 == "\t[Key] " entitlement {
                getline
                getline
                if ($0 == "\t\t[Bool] true") found = 1
            }
            END { exit found ? 0 : 1 }
        ' "$AEGIS_TEMPORARY"
        then
            fail "Entitlements firmados" "missing=$entitlement"
            return
        fi
    done
    pass "Entitlements firmados"
}

check_readiness_snapshot() {
    local expected_revision="$1"
    local output
    if output="$("$AEGIS_PYTHON" - "$AEGIS_READINESS" "$expected_revision" <<'PY'
import json
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
expected_revision = sys.argv[2]
try:
    value = json.loads(path.read_text(encoding="utf-8"))
except (OSError, UnicodeError, json.JSONDecodeError):
    print("invalid_readiness_snapshot")
    raise SystemExit(1)

required = {
    "schema_version",
    "build_revision",
    "daemon",
    "security",
    "provider",
    "local_brain_available",
    "microphone",
    "speech_recognition",
    "screen_capture_authorized",
    "computer_control",
    "wake_word",
    "wake_word_enabled",
    "speaker_identity",
}
if set(value) != required or value["schema_version"] != "2.0":
    print("invalid_readiness_schema")
    raise SystemExit(1)
if (
    re.fullmatch(r"[0-9a-f]{40}", value["build_revision"] or "") is None
    or value["build_revision"] != expected_revision
):
    print(
        f"native_build_mismatch expected={expected_revision[:12]} "
        f"found={str(value['build_revision'])[:12]}"
    )
    raise SystemExit(1)

failures = []
expected = {
    "daemon": "online",
    "security": "intact",
    "microphone": "authorized",
    "speech_recognition": "authorized",
    "computer_control": "ready",
    "wake_word": "ready",
    "speaker_identity": "ready",
}
for field, expected_value in expected.items():
    if value[field] != expected_value:
        failures.append(f"{field}={value[field]}")
if value["screen_capture_authorized"] is not True:
    failures.append("screen_capture_authorized=false")
if value["wake_word_enabled"] is not True:
    failures.append("wake_word_enabled=false")
if value["provider"] != "configured" and value["local_brain_available"] is not True:
    failures.append("hybrid_brain=unavailable")
if failures:
    print(" ".join(failures))
    raise SystemExit(1)
print(
    f"provider={value['provider']} local_brain={str(value['local_brain_available']).lower()} "
    "voice=ready computer=ready wake_word=ready speaker=ready"
)
PY
)"; then
        pass "Permisos y capacidades ($output)"
    else
        fail "Permisos y capacidades" "$output"
    fi
}

check_installation() {
    AEGIS_FAILURES=0
    if [[ "$(/usr/bin/uname -m)" == "arm64" ]]; then
        pass "Arquitectura Apple Silicon arm64"
    else
        fail "Arquitectura Apple Silicon" "found=$(/usr/bin/uname -m)"
    fi

    if [[ -x "$AEGIS_PYTHON" && -x "$AEGIS_PROJECT_ROOT/script/aegis.sh" ]]; then
        pass "Runtime Python aislado"
    else
        fail "Runtime Python aislado" "run uv sync --all-groups --no-editable"
    fi

    if [[ -d "$AEGIS_APP" && ! -L "$AEGIS_APP" && -x "$AEGIS_APP_BINARY" ]]; then
        pass "Bundle instalado"
    else
        fail "Bundle instalado" "run ./script/jarvis_beta.sh install"
    fi

    local app_revision=""
    if [[ -f "$AEGIS_APP_INFO" ]]; then
        local identifier architectures
        identifier="$(/usr/bin/plutil -extract CFBundleIdentifier raw "$AEGIS_APP_INFO" 2>/dev/null || true)"
        app_revision="$(/usr/bin/plutil -extract AegisBuildRevision raw "$AEGIS_APP_INFO" 2>/dev/null || true)"
        architectures="$(/usr/bin/lipo -archs "$AEGIS_APP_BINARY" 2>/dev/null || true)"
        if [[ "$identifier" == "ai.aegis.menubar" && " $architectures " == *" arm64 "* ]]; then
            pass "Identidad y binario arm64"
        else
            fail "Identidad y binario arm64" "bundle_id=$identifier architectures=$architectures"
        fi
        if [[ "$AEGIS_SOURCE_REVISION" =~ ^[0-9a-f]{40}$ && "$app_revision" == "$AEGIS_SOURCE_REVISION" ]]; then
            pass "Identidad exacta de compilación (${app_revision:0:12})"
        else
            fail "Identidad exacta de compilación" \
                "source=${AEGIS_SOURCE_REVISION:0:12} app=${app_revision:0:12}"
        fi
        run_check "Firma del bundle" /usr/bin/codesign --verify --deep --strict "$AEGIS_APP"
        check_entitlements
    fi

    run_check "LaunchAgent del daemon" "$AEGIS_PROJECT_ROOT/script/daemon_service.sh" status
    run_check "LaunchAgent de Menu Bar" "$AEGIS_PROJECT_ROOT/script/menu_bar_service.sh" status
    check_private_path "Socket IPC privado" "$AEGIS_SOCKET" socket
    run_check "Diagnóstico local" "$AEGIS_PROJECT_ROOT/script/aegis.sh" doctor
    run_check "IPC, seguridad y proveedores" "$AEGIS_PROJECT_ROOT/script/aegis.sh" daemon-status
    check_private_path "Snapshot privado de readiness" "$AEGIS_READINESS" file
    if [[ -f "$AEGIS_READINESS" && ! -L "$AEGIS_READINESS" ]]; then
        check_readiness_snapshot "$app_revision"
    fi

    if [[ "$AEGIS_FAILURES" -ne 0 ]]; then
        echo "status=error checks_failed=$AEGIS_FAILURES" >&2
        return 1
    fi
    echo "status=ok profile=jarvis_daily_beta"
}

daily_smoke() {
    check_installation
    run_check "Benchmark local 25/25" "$AEGIS_PROJECT_ROOT/script/aegis.sh" acceptance-benchmark
    run_check "Flujos de producción 20/20" \
        "$AEGIS_PROJECT_ROOT/script/aegis.sh" production-workflows
    run_check "Resistencia prolongada P7 400/400" \
        "$AEGIS_PROJECT_ROOT/script/aegis.sh" long-horizon-reliability
    run_check "Autoevaluación privada" "$AEGIS_PROJECT_ROOT/script/aegis.sh" self-evaluation
    run_check "Soak IPC térmicamente consciente" env AEGIS_SOAK_CYCLES=20 \
        "$AEGIS_PROJECT_ROOT/script/aegis.sh" daemon-soak
    if [[ "$AEGIS_FAILURES" -ne 0 ]]; then
        echo "status=error smoke_failed=$AEGIS_FAILURES" >&2
        return 1
    fi
    echo "status=ok smoke=passed network_calls=0"
}

install_beta() {
    if ! "$AEGIS_PROJECT_ROOT/script/local_codesign_identity.sh" status >/dev/null; then
        echo "status=blocked reason=stable_codesign_identity_missing" >&2
        echo "run=./script/local_codesign_identity.sh install" >&2
        return 2
    fi
    "$AEGIS_PROJECT_ROOT/script/menu_bar_service.sh" install
    "$AEGIS_PROJECT_ROOT/script/daemon_service.sh" install
    "$AEGIS_PROJECT_ROOT/script/menu_bar_service.sh" wake-word-on
    echo "status=ok profile=jarvis_daily_beta next=grant_permissions_then_run_daily"
}

case "$AEGIS_ACTION" in
    check)
        check_installation
        ;;
    daily)
        daily_smoke
        ;;
    install)
        install_beta
        ;;
    -h|--help|help)
        echo "usage: $0 [install|check|daily]"
        ;;
    *)
        echo "usage: $0 [install|check|daily]" >&2
        exit 2
        ;;
esac
