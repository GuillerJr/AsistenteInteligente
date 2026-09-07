#!/usr/bin/env bash
set -euo pipefail
umask 077

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
AEGIS_APP_INFO="$HOME/Applications/Jarvis.app/Contents/Info.plist"
AEGIS_APP_SUPPORT="$HOME/Library/Application Support/Aegis"
AEGIS_EVIDENCE="$AEGIS_APP_SUPPORT/runtime-evidence.json"
AEGIS_ENROLLMENT="$AEGIS_APP_SUPPORT/SpeakerEnrollment"
AEGIS_CALIBRATOR="/private/tmp/aegis-menubar-build/out/Products/Debug/jarvis-biometric-calibrator"
AEGIS_TEMPORARY_ROOT=""
AEGIS_EXTERNAL_REPORT="${AEGIS_P10_REPORT_PATH:-}"

cleanup() {
    case "$AEGIS_TEMPORARY_ROOT" in
        /private/tmp/aegis-p10.*)
            /bin/rm -rf -- "$AEGIS_TEMPORARY_ROOT"
            ;;
    esac
}
trap cleanup EXIT INT TERM HUP

if [[ -n "$AEGIS_EXTERNAL_REPORT" ]]; then
    case "$AEGIS_EXTERNAL_REPORT" in
        /private/tmp/aegis-p11.*/voice-qualification.json)
            ;;
        *)
            echo "status=error reason=voice_report_path_invalid" >&2
            exit 2
            ;;
    esac
fi

persist_voice_report() {
    local report="$1"
    local temporary
    [[ -z "$AEGIS_EXTERNAL_REPORT" ]] && return 0
    temporary="$AEGIS_EXTERNAL_REPORT.tmp"
    if [[ -L "$AEGIS_EXTERNAL_REPORT" || -L "$temporary" ]]; then
        echo "status=error reason=voice_report_target_unsafe" >&2
        exit 2
    fi
    printf '%s\n' "$report" >"$temporary"
    /bin/chmod 600 "$temporary"
    /bin/mv -f "$temporary" "$AEGIS_EXTERNAL_REPORT"
}

cd "$AEGIS_PROJECT_ROOT"
if [[ -L "$HOME/Applications/Jarvis.app" || ! -f "$AEGIS_APP_INFO" ]]; then
    echo "status=blocked reason=current_app_not_installed" >&2
    exit 2
fi
AEGIS_SOURCE_REVISION="$(git rev-parse HEAD)"
AEGIS_APP_REVISION="$(
    /usr/bin/plutil -extract AegisBuildRevision raw "$AEGIS_APP_INFO" 2>/dev/null || true
)"
if [[ "$AEGIS_APP_REVISION" != "$AEGIS_SOURCE_REVISION" ]]; then
    echo "status=blocked reason=installed_app_revision_mismatch" >&2
    exit 2
fi

echo "[aegis-p10] Revalidating P9 before opening the microphone"
./script/p9_browser_gate.sh

echo "[aegis-p10] Running offline adversarial speaker calibration"
AEGIS_RUN_OWNER_VOICE_QUALIFICATION=1 ./script/jarvis_evolutionary_test_harness.py
if [[ ! -x "$AEGIS_CALIBRATOR" ]]; then
    echo "status=blocked reason=biometric_calibrator_unavailable" >&2
    exit 2
fi

AEGIS_OWNER="$({
    "$AEGIS_PROJECT_ROOT/.venv/bin/python" - "$AEGIS_ENROLLMENT" <<'PY'
from __future__ import annotations

import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
owners = sorted(
    item.name
    for item in root.iterdir()
    if item.is_dir()
    and not item.is_symlink()
    and item.name != "background"
    and re.fullmatch(r"[a-z0-9][a-z0-9_-]{1,31}", item.name)
)
if len(owners) != 1:
    raise SystemExit(2)
print(owners[0])
PY
} 2>/dev/null)" || {
    echo "status=blocked reason=single_owner_enrollment_required" >&2
    exit 2
}

AEGIS_TEMPORARY_ROOT="$(/usr/bin/mktemp -d /private/tmp/aegis-p10.XXXXXX)"
AEGIS_CALIBRATION_REPORT="$AEGIS_TEMPORARY_ROOT/speaker-calibration.json"
"$AEGIS_CALIBRATOR" "$AEGIS_OWNER" >"$AEGIS_CALIBRATION_REPORT"
/bin/chmod 600 "$AEGIS_CALIBRATION_REPORT"

voice_report() {
    AEGIS_VOICE_CALIBRATION_REPORT="$AEGIS_CALIBRATION_REPORT" \
        ./script/aegis.sh voice-qualification
}

AEGIS_REPORT=""
AEGIS_REPORT_STATUS=0
if AEGIS_REPORT="$(voice_report)"; then
    persist_voice_report "$AEGIS_REPORT"
    printf '%s\n' "$AEGIS_REPORT"
    echo "status=ok block=P10 owner_voice=qualified"
    exit 0
else
    AEGIS_REPORT_STATUS=$?
fi
persist_voice_report "$AEGIS_REPORT"
printf '%s\n' "$AEGIS_REPORT"
if [[ "$AEGIS_REPORT_STATUS" -eq 2 ]]; then
    echo "status=blocked block=P10 reason=resolve_runtime_blocker" >&2
    exit 2
fi

echo "[aegis-p10] Live owner flow required; no audio or transcript will be persisted"
echo "[aegis-p10] 1/3 Di 'Jarvis'. Tras el tono pide una explicación larga."
echo "[aegis-p10] 2/3 Mientras responda, di 'Jarvis' y luego 'Responde solo: interrupción verificada'."
echo "[aegis-p10] 3/3 En los cinco segundos posteriores, sin repetir Jarvis, di 'Responde solo: seguimiento verificado'."
./script/menu_bar_service.sh wake-word-on >/dev/null

AEGIS_LAST_MTIME="$(/usr/bin/stat -f '%m' "$AEGIS_EVIDENCE" 2>/dev/null || echo 0)"
AEGIS_DEADLINE=$((SECONDS + 240))
while (( SECONDS < AEGIS_DEADLINE )); do
    sleep 0.5
    AEGIS_CURRENT_MTIME="$(/usr/bin/stat -f '%m' "$AEGIS_EVIDENCE" 2>/dev/null || echo 0)"
    if [[ "$AEGIS_CURRENT_MTIME" == "$AEGIS_LAST_MTIME" ]]; then
        continue
    fi
    AEGIS_LAST_MTIME="$AEGIS_CURRENT_MTIME"
    if AEGIS_REPORT="$(voice_report)"; then
        persist_voice_report "$AEGIS_REPORT"
        printf '%s\n' "$AEGIS_REPORT"
        echo "status=ok block=P10 owner_voice=qualified"
        exit 0
    else
        AEGIS_REPORT_STATUS=$?
    fi
    persist_voice_report "$AEGIS_REPORT"
    if [[ "$AEGIS_REPORT_STATUS" -eq 2 ]]; then
        printf '%s\n' "$AEGIS_REPORT" >&2
        echo "status=blocked block=P10 reason=runtime_changed_during_voice_flow" >&2
        exit 2
    fi
done

printf '%s\n' "$AEGIS_REPORT" >&2
echo "status=needs_interaction block=P10 reason=live_voice_flow_incomplete" >&2
exit 1
