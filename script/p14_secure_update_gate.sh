#!/usr/bin/env bash
set -euo pipefail
umask 077

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_FINAL_REPORT="$AEGIS_PROJECT_ROOT/dist/Jarvis.update-qualification.json"
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

./script/p13_self_contained_runtime_gate.sh
./script/release_macos.sh channel

AEGIS_TEMPORARY_REPORT="$(
    /usr/bin/mktemp "$AEGIS_PROJECT_ROOT/dist/.Jarvis.update-qualification.XXXXXX"
)"
/bin/chmod 600 "$AEGIS_TEMPORARY_REPORT"
AEGIS_P14_P13_REPORT="$AEGIS_PROJECT_ROOT/dist/Jarvis.runtime.json" \
AEGIS_P14_UPDATE_MANIFEST="$AEGIS_PROJECT_ROOT/dist/Jarvis.update.json" \
AEGIS_P14_RELEASE_MANIFEST="$AEGIS_PROJECT_ROOT/dist/Jarvis.release.json" \
AEGIS_P14_RELEASE_ARCHIVE="$AEGIS_PROJECT_ROOT/dist/Jarvis.zip" \
AEGIS_P14_PUBLIC_KEY="$AEGIS_PROJECT_ROOT/packaging/JarvisUpdatePublicKey.ed25519" \
    "$AEGIS_PROJECT_ROOT/script/aegis.sh" \
        secure-update-qualification >"$AEGIS_TEMPORARY_REPORT"
/bin/mv -f "$AEGIS_TEMPORARY_REPORT" "$AEGIS_FINAL_REPORT"
AEGIS_TEMPORARY_REPORT=""
/bin/chmod 600 "$AEGIS_FINAL_REPORT"
echo "status=ok block=P14 score=100 report=$AEGIS_FINAL_REPORT"
