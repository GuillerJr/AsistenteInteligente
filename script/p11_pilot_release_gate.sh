#!/usr/bin/env bash
set -euo pipefail
umask 077

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
AEGIS_INSTALLED_INFO="$HOME/Applications/Jarvis.app/Contents/Info.plist"
AEGIS_TEMPORARY_ROOT=""
AEGIS_TRANSIENT_REPORT=""
AEGIS_FINAL_REPORT="$AEGIS_PROJECT_ROOT/dist/Jarvis.pilot.json"

cleanup() {
    case "$AEGIS_TRANSIENT_REPORT" in
        "$AEGIS_PROJECT_ROOT"/dist/.Jarvis.pilot.*)
            /bin/rm -f -- "$AEGIS_TRANSIENT_REPORT"
            ;;
    esac
    case "$AEGIS_TEMPORARY_ROOT" in
        /private/tmp/aegis-p11.*)
            /bin/rm -rf -- "$AEGIS_TEMPORARY_ROOT"
            ;;
    esac
}
trap cleanup EXIT INT TERM HUP

cd "$AEGIS_PROJECT_ROOT"
if [[ -L "$HOME/Applications/Jarvis.app" || ! -f "$AEGIS_INSTALLED_INFO" ]]; then
    echo "status=blocked reason=current_app_not_installed" >&2
    exit 2
fi
AEGIS_SOURCE_REVISION="$(git rev-parse --verify HEAD)"
AEGIS_APP_REVISION="$(
    /usr/bin/plutil -extract AegisBuildRevision raw "$AEGIS_INSTALLED_INFO" 2>/dev/null || true
)"
if [[ ! "$AEGIS_SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
    echo "status=error reason=source_revision_invalid" >&2
    exit 2
fi
if [[ "$AEGIS_APP_REVISION" != "$AEGIS_SOURCE_REVISION" ]]; then
    echo "status=blocked reason=installed_app_revision_mismatch" >&2
    exit 2
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "status=blocked reason=tracked_source_tree_dirty" >&2
    exit 2
fi

AEGIS_TEMPORARY_ROOT="$(/usr/bin/mktemp -d /private/tmp/aegis-p11.XXXXXX)"
/bin/chmod 700 "$AEGIS_TEMPORARY_ROOT"
AEGIS_VOICE_REPORT="$AEGIS_TEMPORARY_ROOT/voice-qualification.json"
AEGIS_MACOS_REPORT="$AEGIS_TEMPORARY_ROOT/macos-qualification.json"

echo "[aegis-p11] Revalidating the complete P7-P10 chain"
set +e
AEGIS_P10_REPORT_PATH="$AEGIS_VOICE_REPORT" ./script/p10_voice_gate.sh
AEGIS_P10_STATUS=$?
set -e
if [[ "$AEGIS_P10_STATUS" -ne 0 ]]; then
    echo "status=blocked block=P11 prerequisite=P10" >&2
    exit "$AEGIS_P10_STATUS"
fi
if [[ -L "$AEGIS_VOICE_REPORT" || ! -f "$AEGIS_VOICE_REPORT" ]]; then
    echo "status=error reason=voice_qualification_evidence_missing" >&2
    exit 2
fi

echo "[aegis-p11] Qualifying the live macOS runtime"
if ! AEGIS_MACOS_OUTPUT="$(./script/aegis.sh macos-qualification)"; then
    printf '%s\n' "$AEGIS_MACOS_OUTPUT" >&2
    echo "status=blocked block=P11 prerequisite=macos_qualification" >&2
    exit 2
fi
printf '%s\n' "$AEGIS_MACOS_OUTPUT" >"$AEGIS_MACOS_REPORT"
/bin/chmod 600 "$AEGIS_MACOS_REPORT"
printf '%s\n' "$AEGIS_MACOS_OUTPUT"

echo "[aegis-p11] Building and authenticating the privacy-safe pilot candidate"
./script/release_macos.sh candidate
if [[ -L "$AEGIS_PROJECT_ROOT/dist" ]]; then
    echo "status=error reason=release_evidence_directory_unsafe" >&2
    exit 2
fi

echo "[aegis-p11] Binding all evidence to one exact revision"
set +e
AEGIS_P11_OUTPUT="$(
    AEGIS_P11_VOICE_REPORT="$AEGIS_VOICE_REPORT" \
    AEGIS_P11_MACOS_REPORT="$AEGIS_MACOS_REPORT" \
    AEGIS_P11_RELEASE_MANIFEST="$AEGIS_PROJECT_ROOT/dist/Jarvis.release.json" \
    AEGIS_P11_RELEASE_SBOM="$AEGIS_PROJECT_ROOT/dist/Jarvis.spdx.json" \
    AEGIS_P11_RELEASE_ARCHIVE="$AEGIS_PROJECT_ROOT/dist/Jarvis.zip" \
        ./script/aegis.sh pilot-release-qualification
)"
AEGIS_P11_STATUS=$?
set -e
printf '%s\n' "$AEGIS_P11_OUTPUT"
if [[ "$AEGIS_P11_STATUS" -ne 0 ]]; then
    echo "status=blocked block=P11 reason=evidence_rejected" >&2
    exit "$AEGIS_P11_STATUS"
fi

AEGIS_TRANSIENT_REPORT="$(
    /usr/bin/mktemp "$AEGIS_PROJECT_ROOT/dist/.Jarvis.pilot.XXXXXX"
)"
printf '%s\n' "$AEGIS_P11_OUTPUT" >"$AEGIS_TRANSIENT_REPORT"
/bin/chmod 600 "$AEGIS_TRANSIENT_REPORT"
/bin/mv -f "$AEGIS_TRANSIENT_REPORT" "$AEGIS_FINAL_REPORT"
AEGIS_TRANSIENT_REPORT=""
echo "status=ok block=P11 profile=local-pilot evidence=dist/Jarvis.pilot.json"
