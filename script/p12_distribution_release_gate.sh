#!/usr/bin/env bash
set -euo pipefail
umask 077

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
AEGIS_PILOT_REPORT="$AEGIS_PROJECT_ROOT/dist/Jarvis.pilot.json"
AEGIS_RELEASE_MANIFEST="$AEGIS_PROJECT_ROOT/dist/Jarvis.release.json"
AEGIS_RELEASE_SBOM="$AEGIS_PROJECT_ROOT/dist/Jarvis.spdx.json"
AEGIS_RELEASE_ARCHIVE="$AEGIS_PROJECT_ROOT/dist/Jarvis.zip"
AEGIS_FINAL_REPORT="$AEGIS_PROJECT_ROOT/dist/Jarvis.distribution.json"
AEGIS_TRANSIENT_REPORT=""

cleanup() {
    case "$AEGIS_TRANSIENT_REPORT" in
        "$AEGIS_PROJECT_ROOT"/dist/.Jarvis.distribution.*)
            /bin/rm -f -- "$AEGIS_TRANSIENT_REPORT"
            ;;
    esac
}
trap cleanup EXIT INT TERM HUP

cd "$AEGIS_PROJECT_ROOT"
if [[ -z "${AEGIS_CODESIGN_IDENTITY:-}" ]]; then
    echo "status=blocked block=P12 reason=developer_id_identity_missing" >&2
    exit 2
fi
if [[ "$AEGIS_CODESIGN_IDENTITY" != "Developer ID Application:"* ]]; then
    echo "status=blocked block=P12 reason=developer_id_application_identity_required" >&2
    exit 2
fi
if [[ -z "${AEGIS_NOTARY_PROFILE:-}" ]]; then
    echo "status=blocked block=P12 reason=notary_profile_missing" >&2
    exit 2
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "status=blocked block=P12 reason=tracked_source_tree_dirty" >&2
    exit 2
fi

echo "[aegis-p12] Revalidating the complete local pilot chain"
./script/p11_pilot_release_gate.sh
if [[ -L "$AEGIS_PILOT_REPORT" || ! -f "$AEGIS_PILOT_REPORT" ]]; then
    echo "status=blocked block=P12 reason=pilot_evidence_missing" >&2
    exit 2
fi

echo "[aegis-p12] Building, signing and submitting the privacy-safe distribution"
./script/release_macos.sh notarize

for artifact in \
    "$AEGIS_RELEASE_MANIFEST" \
    "$AEGIS_RELEASE_SBOM" \
    "$AEGIS_RELEASE_ARCHIVE"; do
    if [[ -L "$artifact" || ! -f "$artifact" ]]; then
        echo "status=blocked block=P12 reason=distribution_evidence_missing" >&2
        exit 2
    fi
done

echo "[aegis-p12] Independently assessing the notarized ZIP with codesign, stapler and Gatekeeper"
set +e
AEGIS_P12_OUTPUT="$(
    AEGIS_P12_PILOT_REPORT="$AEGIS_PILOT_REPORT" \
    AEGIS_P12_RELEASE_MANIFEST="$AEGIS_RELEASE_MANIFEST" \
    AEGIS_P12_RELEASE_SBOM="$AEGIS_RELEASE_SBOM" \
    AEGIS_P12_RELEASE_ARCHIVE="$AEGIS_RELEASE_ARCHIVE" \
    AEGIS_P12_CODESIGN_IDENTITY="$AEGIS_CODESIGN_IDENTITY" \
        ./script/aegis.sh distribution-release-qualification
)"
AEGIS_P12_STATUS=$?
set -e
printf '%s\n' "$AEGIS_P12_OUTPUT"
if [[ "$AEGIS_P12_STATUS" -ne 0 ]]; then
    echo "status=blocked block=P12 reason=distribution_evidence_rejected" >&2
    exit "$AEGIS_P12_STATUS"
fi

AEGIS_TRANSIENT_REPORT="$(
    /usr/bin/mktemp "$AEGIS_PROJECT_ROOT/dist/.Jarvis.distribution.XXXXXX"
)"
printf '%s\n' "$AEGIS_P12_OUTPUT" >"$AEGIS_TRANSIENT_REPORT"
/bin/chmod 600 "$AEGIS_TRANSIENT_REPORT"
/bin/mv -f "$AEGIS_TRANSIENT_REPORT" "$AEGIS_FINAL_REPORT"
AEGIS_TRANSIENT_REPORT=""
echo "status=ok block=P12 profile=notarized-distribution evidence=dist/Jarvis.distribution.json"
