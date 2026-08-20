#!/usr/bin/env bash
set -euo pipefail

AEGIS_ACTION="${1:-check}"
AEGIS_APP_NAME="AegisMenuBar"
AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_APP_BUNDLE="/private/tmp/$AEGIS_APP_NAME.app"
AEGIS_ARCHIVE="$AEGIS_PROJECT_ROOT/dist/$AEGIS_APP_NAME.zip"
AEGIS_IDENTITY="${AEGIS_CODESIGN_IDENTITY:-}"
AEGIS_NOTARY_PROFILE="${AEGIS_NOTARY_PROFILE:-}"

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
    AEGIS_CODESIGN_IDENTITY="$AEGIS_IDENTITY" \
        "$AEGIS_PROJECT_ROOT/script/build_and_run.sh" package
    inspect_bundle
    validate_distribution_signature
    /usr/bin/unzip -tqq "$AEGIS_ARCHIVE"
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
        /usr/bin/xcrun notarytool submit "$AEGIS_ARCHIVE" \
            --keychain-profile "$AEGIS_NOTARY_PROFILE" \
            --wait
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
