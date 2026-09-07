#!/usr/bin/env bash
set -euo pipefail

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
AEGIS_INSTALLED_APP="$HOME/Applications/Jarvis.app"
AEGIS_INSTALLED_INFO="$AEGIS_INSTALLED_APP/Contents/Info.plist"
AEGIS_SOAK_CYCLES="${AEGIS_P7_SOAK_CYCLES:-100}"
cd "$AEGIS_PROJECT_ROOT"

if [[ ! "$AEGIS_SOAK_CYCLES" =~ ^[1-9][0-9]*$ ]]; then
    echo "status=error reason=invalid_p7_soak_cycles" >&2
    exit 2
fi
if (( AEGIS_SOAK_CYCLES < 20 || AEGIS_SOAK_CYCLES > 10000 )); then
    echo "status=error reason=p7_soak_cycles_out_of_range" >&2
    exit 2
fi
if [[ -L "$AEGIS_INSTALLED_APP" || ! -f "$AEGIS_INSTALLED_INFO" ]]; then
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

echo "[aegis-p7] Verifying the installed runtime without owner-voice input"
./script/jarvis_beta.sh check

echo "[aegis-p7] Running the 400-flow long-horizon workload"
./script/aegis.sh long-horizon-reliability

echo "[aegis-p7] Soaking signed IPC in the current thermal mode"
AEGIS_SOAK_CYCLES="$AEGIS_SOAK_CYCLES" ./script/aegis.sh daemon-soak

echo "status=ok block=P7 owner_voice=deferred"
