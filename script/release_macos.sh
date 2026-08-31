#!/usr/bin/env bash
set -euo pipefail
umask 077

AEGIS_ACTION="${1:-check}"
AEGIS_APP_NAME="Jarvis"
AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_APP_BUNDLE="/private/tmp/$AEGIS_APP_NAME.app"
AEGIS_ARCHIVE="$AEGIS_PROJECT_ROOT/dist/$AEGIS_APP_NAME.zip"
AEGIS_NOTARY_LOG="$AEGIS_PROJECT_ROOT/dist/$AEGIS_APP_NAME.notarization.json"
AEGIS_IDENTITY="${AEGIS_CODESIGN_IDENTITY:-}"
AEGIS_NOTARY_PROFILE="${AEGIS_NOTARY_PROFILE:-}"
AEGIS_NOTARY_TIMEOUT_SECONDS="${AEGIS_NOTARY_TIMEOUT_SECONDS:-3600}"
AEGIS_NOTARY_POLL_SECONDS="${AEGIS_NOTARY_POLL_SECONDS:-15}"
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
    AEGIS_BUILD_MLX=1 AEGIS_CODESIGN_IDENTITY="$AEGIS_IDENTITY" \
        "$AEGIS_PROJECT_ROOT/script/build_and_run.sh" package
    inspect_bundle
    validate_distribution_signature
    /usr/bin/unzip -tqq "$AEGIS_ARCHIVE"
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
        echo "status=ok artifact=distribution-ready"
        ;;
    archive)
        build_archive
        echo "status=ok artifact=$AEGIS_ARCHIVE"
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
        /usr/sbin/spctl --assess --type execute --verbose=4 "$AEGIS_APP_BUNDLE"
        echo "status=ok artifact=$AEGIS_ARCHIVE notarized=true"
        ;;
    *)
        echo "usage: $0 [check|archive|notarize]" >&2
        exit 2
        ;;
esac
