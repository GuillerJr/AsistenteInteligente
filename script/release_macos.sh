#!/usr/bin/env bash
set -euo pipefail
umask 077

AEGIS_ACTION="${1:-check}"
AEGIS_APP_NAME="Jarvis"
AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_APP_BUNDLE="/private/tmp/$AEGIS_APP_NAME.app"
AEGIS_ARCHIVE="$AEGIS_PROJECT_ROOT/dist/$AEGIS_APP_NAME.zip"
AEGIS_NOTARY_LOG="$AEGIS_PROJECT_ROOT/dist/$AEGIS_APP_NAME.notarization.json"
AEGIS_RELEASE_MANIFEST="$AEGIS_PROJECT_ROOT/dist/$AEGIS_APP_NAME.release.json"
AEGIS_RELEASE_SBOM="$AEGIS_PROJECT_ROOT/dist/$AEGIS_APP_NAME.spdx.json"
AEGIS_UPDATE_MANIFEST="$AEGIS_PROJECT_ROOT/dist/$AEGIS_APP_NAME.update.json"
AEGIS_UPDATE_PUBLIC_KEY="$AEGIS_PROJECT_ROOT/packaging/JarvisUpdatePublicKey.ed25519"
AEGIS_UPDATE_TOOL="$AEGIS_PROJECT_ROOT/script/update_channel.py"
AEGIS_EVIDENCE_TOOL="$AEGIS_PROJECT_ROOT/script/release_evidence.py"
AEGIS_PYTHON="$AEGIS_PROJECT_ROOT/.venv/bin/python"
AEGIS_IDENTITY="${AEGIS_CODESIGN_IDENTITY:-}"
AEGIS_NOTARY_PROFILE="${AEGIS_NOTARY_PROFILE:-}"
AEGIS_NOTARY_TIMEOUT_SECONDS="${AEGIS_NOTARY_TIMEOUT_SECONDS:-3600}"
AEGIS_NOTARY_POLL_SECONDS="${AEGIS_NOTARY_POLL_SECONDS:-15}"
AEGIS_RELEASE_BUILD_NUMBER="${AEGIS_BUILD_NUMBER:-$(/bin/date -u +%s)}"
AEGIS_NOTARY_TEMPORARY_ROOT=""

cleanup_notary_temporary_root() {
    if [[
        -n "$AEGIS_NOTARY_TEMPORARY_ROOT"
        && "$AEGIS_NOTARY_TEMPORARY_ROOT" == /private/tmp/aegis-notary.*
        && -d "$AEGIS_NOTARY_TEMPORARY_ROOT"
        && ! -L "$AEGIS_NOTARY_TEMPORARY_ROOT"
    ]]; then
        /bin/rm -rf -- "$AEGIS_NOTARY_TEMPORARY_ROOT"
    fi
}

trap cleanup_notary_temporary_root EXIT

die() {
    echo "status=error reason=$1" >&2
    exit "${2:-1}"
}

require_positive_integer() {
    local value="$1"
    local name="$2"
    if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
        die "invalid_$name" 2
    fi
}

inspect_bundle() {
    test -d "$AEGIS_APP_BUNDLE"
    test -x "$AEGIS_APP_BUNDLE/Contents/MacOS/$AEGIS_APP_NAME"
    /usr/bin/plutil -lint "$AEGIS_APP_BUNDLE/Contents/Info.plist" >/dev/null
    /usr/bin/file "$AEGIS_APP_BUNDLE/Contents/MacOS/$AEGIS_APP_NAME" | /usr/bin/grep -q arm64
    /usr/bin/codesign --verify --deep --strict --verbose=2 "$AEGIS_APP_BUNDLE"
}

release_evidence() {
    local action="$1"
    local profile="$2"
    test -x "$AEGIS_PYTHON"
    test -f "$AEGIS_EVIDENCE_TOOL"
    "$AEGIS_PYTHON" "$AEGIS_EVIDENCE_TOOL" "$action" \
        --project-root "$AEGIS_PROJECT_ROOT" \
        --archive "$AEGIS_ARCHIVE" \
        --manifest "$AEGIS_RELEASE_MANIFEST" \
        --sbom "$AEGIS_RELEASE_SBOM" \
        --profile "$profile"
}

sign_update_channel() {
    test -x "$AEGIS_UPDATE_TOOL"
    "$AEGIS_PYTHON" "$AEGIS_UPDATE_TOOL" status \
        --public-key "$AEGIS_UPDATE_PUBLIC_KEY"
    "$AEGIS_PYTHON" "$AEGIS_UPDATE_TOOL" sign \
        --public-key "$AEGIS_UPDATE_PUBLIC_KEY" \
        --archive "$AEGIS_ARCHIVE" \
        --release-manifest "$AEGIS_RELEASE_MANIFEST" \
        --update-manifest "$AEGIS_UPDATE_MANIFEST"
    "$AEGIS_PYTHON" "$AEGIS_UPDATE_TOOL" verify \
        --public-key "$AEGIS_UPDATE_PUBLIC_KEY" \
        --archive "$AEGIS_ARCHIVE" \
        --release-manifest "$AEGIS_RELEASE_MANIFEST" \
        --update-manifest "$AEGIS_UPDATE_MANIFEST" \
        --current-build 0
}

manifest_profile() {
    "$AEGIS_PYTHON" -c '
import json, pathlib, sys
path = pathlib.Path(sys.argv[1])
if path.is_symlink() or not path.is_file():
    raise SystemExit(1)
value = json.loads(path.read_text(encoding="utf-8"))
profile = value.get("profile") if isinstance(value, dict) else None
if profile not in {"developer-id", "developer-id-notarized"}:
    raise SystemExit(1)
print(profile)
' "$AEGIS_RELEASE_MANIFEST"
}

require_distribution_identity() {
    if [[ -z "$AEGIS_IDENTITY" || "$AEGIS_IDENTITY" == "-" ]]; then
        echo "status=blocked reason=developer_id_identity_missing" >&2
        return 2
    fi
    if [[ "$AEGIS_IDENTITY" != "Developer ID Application:"* ]]; then
        echo "status=blocked reason=developer_id_application_identity_required" >&2
        return 2
    fi
    if ! /usr/bin/security find-identity -v -p codesigning | /usr/bin/grep -Fq "$AEGIS_IDENTITY"; then
        echo "status=blocked reason=developer_id_identity_unavailable" >&2
        return 2
    fi
}

validate_distribution_signature() {
    local signature
    signature="$(/usr/bin/codesign -dvvv "$AEGIS_APP_BUNDLE" 2>&1)"
    if [[ "$signature" == *"Signature=adhoc"* || "$signature" == *"TeamIdentifier=not set"* ]]; then
        echo "status=blocked reason=developer_id_signature_missing" >&2
        return 2
    fi
    if [[ "$signature" != *"runtime"* ]]; then
        echo "status=error reason=distribution_signature_invalid" >&2
        return 1
    fi
}

build_archive() {
    require_distribution_identity
    require_positive_integer "$AEGIS_RELEASE_BUILD_NUMBER" "build_number"
    if (( AEGIS_RELEASE_BUILD_NUMBER > 2147483647 )); then
        die "invalid_build_number" 2
    fi
    AEGIS_BUILD_MLX=1 AEGIS_INCLUDE_PERSONAL_MODELS=0 AEGIS_EMIT_ARCHIVE=1 \
        AEGIS_EMBED_DAEMON=1 \
        AEGIS_BUILD_NUMBER="$AEGIS_RELEASE_BUILD_NUMBER" \
        AEGIS_CODESIGN_IDENTITY="$AEGIS_IDENTITY" \
        "$AEGIS_PROJECT_ROOT/script/build_and_run.sh" package
    inspect_bundle
    validate_distribution_signature
    /usr/bin/unzip -tqq "$AEGIS_ARCHIVE"
    release_evidence generate developer-id
    release_evidence verify developer-id
}

build_local_candidate() {
    AEGIS_INCLUDE_PERSONAL_MODELS=0 AEGIS_EMIT_ARCHIVE=1 AEGIS_EMBED_DAEMON=1 \
        "$AEGIS_PROJECT_ROOT/script/build_and_run.sh" package
    inspect_bundle
    /usr/bin/unzip -tqq "$AEGIS_ARCHIVE"
    release_evidence generate local-development
    release_evidence verify local-development
}

notary_plist_value() {
    local plist_path="$1"
    local key="$2"
    /usr/libexec/PlistBuddy -c "Print :$key" "$plist_path" 2>/dev/null
}

write_notary_log() {
    local submission_id="$1"
    /bin/rm -f -- "$AEGIS_NOTARY_LOG"
    if ! /usr/bin/xcrun notarytool log "$submission_id" \
        --keychain-profile "$AEGIS_NOTARY_PROFILE" \
        "$AEGIS_NOTARY_LOG"; then
        echo "status=warning reason=notary_log_unavailable submission_id=$submission_id" >&2
        return
    fi
    /bin/chmod 600 "$AEGIS_NOTARY_LOG"
}

submit_and_wait_for_notarization() {
    local submission_plist
    local status_plist
    local submission_id
    local verdict
    local started_at
    local now
    local consecutive_failures=0

    require_positive_integer "$AEGIS_NOTARY_TIMEOUT_SECONDS" "notary_timeout_seconds"
    require_positive_integer "$AEGIS_NOTARY_POLL_SECONDS" "notary_poll_seconds"
    AEGIS_NOTARY_TEMPORARY_ROOT="$(
        /usr/bin/mktemp -d /private/tmp/aegis-notary.XXXXXX
    )"
    /bin/chmod 700 "$AEGIS_NOTARY_TEMPORARY_ROOT"
    submission_plist="$AEGIS_NOTARY_TEMPORARY_ROOT/submission.plist"
    status_plist="$AEGIS_NOTARY_TEMPORARY_ROOT/status.plist"

    /usr/bin/xcrun notarytool submit "$AEGIS_ARCHIVE" \
        --keychain-profile "$AEGIS_NOTARY_PROFILE" \
        --output-format plist >"$submission_plist"
    submission_id="$(notary_plist_value "$submission_plist" id || true)"
    if [[ ! "$submission_id" =~ ^[0-9A-Fa-f-]{36}$ ]]; then
        die "notary_submission_id_invalid"
    fi

    started_at="$SECONDS"
    while true; do
        if ! /usr/bin/xcrun notarytool info "$submission_id" \
            --keychain-profile "$AEGIS_NOTARY_PROFILE" \
            --output-format plist >"$status_plist"; then
            consecutive_failures=$((consecutive_failures + 1))
            if (( consecutive_failures >= 3 )); then
                write_notary_log "$submission_id"
                die "notary_status_unavailable"
            fi
            /bin/sleep "$AEGIS_NOTARY_POLL_SECONDS"
            continue
        fi
        consecutive_failures=0
        verdict="$(notary_plist_value "$status_plist" status || true)"
        case "$verdict" in
            Accepted)
                write_notary_log "$submission_id"
                echo "status=accepted submission_id=$submission_id"
                return 0
                ;;
            Invalid|Rejected)
                write_notary_log "$submission_id"
                die "notarization_rejected"
                ;;
            In\ Progress|Submitted)
                ;;
            *)
                write_notary_log "$submission_id"
                die "notary_status_invalid"
                ;;
        esac
        now="$SECONDS"
        if (( now - started_at >= AEGIS_NOTARY_TIMEOUT_SECONDS )); then
            write_notary_log "$submission_id"
            die "notarization_timeout"
        fi
        /bin/sleep "$AEGIS_NOTARY_POLL_SECONDS"
    done
}

case "$AEGIS_ACTION" in
    check)
        inspect_bundle
        if ! validate_distribution_signature; then
            exit 2
        fi
        AEGIS_CURRENT_PROFILE="$(manifest_profile)" || die "release_manifest_invalid"
        release_evidence verify "$AEGIS_CURRENT_PROFILE"
        echo "status=ok artifact=distribution-ready profile=$AEGIS_CURRENT_PROFILE"
        ;;
    candidate)
        build_local_candidate
        echo "status=ok artifact=$AEGIS_ARCHIVE profile=local-development evidence=$AEGIS_RELEASE_MANIFEST sbom=$AEGIS_RELEASE_SBOM"
        ;;
    archive)
        build_archive
        echo "status=ok artifact=$AEGIS_ARCHIVE profile=developer-id evidence=$AEGIS_RELEASE_MANIFEST sbom=$AEGIS_RELEASE_SBOM"
        ;;
    notarize)
        if [[ -z "$AEGIS_NOTARY_PROFILE" ]]; then
            echo "status=blocked reason=notary_profile_missing" >&2
            exit 2
        fi
        build_archive
        submit_and_wait_for_notarization
        /usr/bin/xcrun stapler staple "$AEGIS_APP_BUNDLE"
        /usr/bin/xcrun stapler validate "$AEGIS_APP_BUNDLE"
        /bin/rm -f "$AEGIS_ARCHIVE"
        /usr/bin/ditto -c -k --norsrc --keepParent "$AEGIS_APP_BUNDLE" "$AEGIS_ARCHIVE"
        /usr/bin/unzip -tqq "$AEGIS_ARCHIVE"
        release_evidence generate developer-id-notarized
        release_evidence verify developer-id-notarized
        sign_update_channel
        /usr/sbin/spctl --assess --type execute --verbose=4 "$AEGIS_APP_BUNDLE"
        echo "status=ok artifact=$AEGIS_ARCHIVE notarized=true evidence=$AEGIS_RELEASE_MANIFEST sbom=$AEGIS_RELEASE_SBOM update=$AEGIS_UPDATE_MANIFEST"
        ;;
    channel)
        release_evidence verify developer-id-notarized
        sign_update_channel
        echo "status=ok channel=stable update=$AEGIS_UPDATE_MANIFEST"
        ;;
    *)
        echo "usage: $0 [check|candidate|archive|notarize|channel]" >&2
        exit 2
        ;;
esac
