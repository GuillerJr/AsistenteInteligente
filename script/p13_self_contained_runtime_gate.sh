#!/usr/bin/env bash
set -euo pipefail
umask 077

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_FINAL_REPORT="$AEGIS_PROJECT_ROOT/dist/Jarvis.runtime.json"
AEGIS_TEMPORARY_REPORT=""

cleanup() {
    if [[ -n "$AEGIS_TEMPORARY_REPORT" && -f "$AEGIS_TEMPORARY_REPORT" ]]; then
        /bin/rm -f -- "$AEGIS_TEMPORARY_REPORT"
    fi
}
trap cleanup EXIT

cd "$AEGIS_PROJECT_ROOT"
if ! /usr/bin/git diff --quiet || ! /usr/bin/git diff --cached --quiet; then
    echo "status=blocked reason=tracked_source_tree_dirty" >&2
    exit 2
fi

./script/p12_distribution_release_gate.sh

AEGIS_TEMPORARY_REPORT="$(/usr/bin/mktemp "$AEGIS_PROJECT_ROOT/dist/.Jarvis.runtime.XXXXXX")"
/bin/chmod 600 "$AEGIS_TEMPORARY_REPORT"
AEGIS_P13_P12_REPORT="$AEGIS_PROJECT_ROOT/dist/Jarvis.distribution.json" \
AEGIS_P13_RELEASE_MANIFEST="$AEGIS_PROJECT_ROOT/dist/Jarvis.release.json" \
AEGIS_P13_RELEASE_ARCHIVE="$AEGIS_PROJECT_ROOT/dist/Jarvis.zip" \
    "$AEGIS_PROJECT_ROOT/script/aegis.sh" \
        self-contained-release-qualification >"$AEGIS_TEMPORARY_REPORT"
/bin/mv -f "$AEGIS_TEMPORARY_REPORT" "$AEGIS_FINAL_REPORT"
AEGIS_TEMPORARY_REPORT=""
/bin/chmod 600 "$AEGIS_FINAL_REPORT"
echo "status=ok block=P13 score=100 report=$AEGIS_FINAL_REPORT"
