#!/usr/bin/env bash
set -euo pipefail

AEGIS_REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
AEGIS_INSTALLED_APP="$HOME/Applications/Jarvis.app"
AEGIS_INSTALLED_INFO="$AEGIS_INSTALLED_APP/Contents/Info.plist"
cd "$AEGIS_REPOSITORY_ROOT"

echo "[aegis-hardware-gate] Verifying the installed signed Swift application"
if [[ -L "$AEGIS_INSTALLED_APP" || ! -f "$AEGIS_INSTALLED_INFO" ]]; then
    echo "[aegis-hardware-gate] Rejected: install the current Jarvis.app first" >&2
    exit 1
fi
AEGIS_SOURCE_REVISION="$(git rev-parse HEAD)"
AEGIS_APP_REVISION="$(
    /usr/bin/plutil -extract AegisBuildRevision raw "$AEGIS_INSTALLED_INFO" 2>/dev/null || true
)"
if [[ "$AEGIS_APP_REVISION" != "$AEGIS_SOURCE_REVISION" ]]; then
    echo "[aegis-hardware-gate] Rejected: installed app revision is stale" >&2
    exit 1
fi
/usr/bin/codesign --verify --deep --strict "$AEGIS_INSTALLED_APP"
./script/menu_bar_service.sh status

echo "[aegis-hardware-gate] Running deterministic local contracts"
./script/aegis.sh acceptance-benchmark
./script/aegis.sh production-workflows

echo "[aegis-hardware-gate] Running live macOS qualification"
if ! AEGIS_QUALIFICATION_OUTPUT="$(./script/aegis.sh macos-qualification)"; then
    printf '%s\n' "$AEGIS_QUALIFICATION_OUTPUT" >&2
    echo "[aegis-hardware-gate] Blocked: live qualification is unavailable" >&2
    exit 1
fi
printf '%s\n' "$AEGIS_QUALIFICATION_OUTPUT"

AEGIS_QUALIFICATION_SCORE="$({
    printf '%s\n' "$AEGIS_QUALIFICATION_OUTPUT" \
        | /usr/bin/sed -n 's/.*"score":\([0-9][0-9]*\).*/\1/p'
} | /usr/bin/tail -n 1)"
if [[
    "$AEGIS_QUALIFICATION_SCORE" != "100"
    || "$AEGIS_QUALIFICATION_OUTPUT" != *'"gate_passed":true'*
]]; then
    echo "[aegis-hardware-gate] Rejected: live macOS score must be 100" >&2
    exit 1
fi

echo "[aegis-hardware-gate] Hardware qualification passed"
