#!/usr/bin/env bash
set -euo pipefail

JARVIS_ACTION="${1:-status}"
JARVIS_IDENTITY_NAME="Jarvis Local Development"
JARVIS_KEYCHAIN_OUTPUT="$(/usr/bin/security default-keychain -d user)"
JARVIS_LOGIN_KEYCHAIN="${JARVIS_KEYCHAIN_OUTPUT#*\"}"
JARVIS_LOGIN_KEYCHAIN="${JARVIS_LOGIN_KEYCHAIN%\"*}"
# The signing key gets its own keychain, locked after 5 minutes and on sleep, so a
# process of this user cannot run codesign with it unless the owner unlocks it.
JARVIS_KEYCHAIN="$HOME/Library/Keychains/jarvis-signing.keychain-db"
JARVIS_TEMPORARY=""
JARVIS_CERTIFICATE_IMPORTED=false
JARVIS_COMPLETE=false

if [[ -z "$JARVIS_LOGIN_KEYCHAIN" || ! -f "$JARVIS_LOGIN_KEYCHAIN" || -L "$JARVIS_LOGIN_KEYCHAIN" ]]; then
    echo "status=error reason=unsafe_default_keychain" >&2
    exit 1
fi
if [[ -L "$JARVIS_KEYCHAIN" ]]; then
    echo "status=error reason=unsafe_signing_keychain" >&2
    exit 1
fi

identity_hash() {
    local keychain="${1:-$JARVIS_KEYCHAIN}"
    [[ -f "$keychain" ]] || return 0
    /usr/bin/security find-identity -v -p codesigning "$keychain" 2>/dev/null \
        | /usr/bin/awk -v name="$JARVIS_IDENTITY_NAME" \
            'index($0, "\"" name "\"") { print $2; exit }'
}

cleanup() {
    if [[ "$JARVIS_CERTIFICATE_IMPORTED" == true && "$JARVIS_COMPLETE" != true ]]; then
        /usr/bin/security delete-identity \
            -c "$JARVIS_IDENTITY_NAME" \
            -t \
            "$JARVIS_KEYCHAIN" >/dev/null 2>&1 \
            || /usr/bin/security delete-certificate \
                -c "$JARVIS_IDENTITY_NAME" \
                -t \
                "$JARVIS_KEYCHAIN" >/dev/null 2>&1 \
            || true
    fi
    if [[ -n "$JARVIS_TEMPORARY" ]]; then
        /bin/rm -rf "$JARVIS_TEMPORARY"
    fi
}

trap cleanup EXIT

legacy_identity_hash() {
    identity_hash "$JARVIS_LOGIN_KEYCHAIN"
}

ensure_signing_keychain() {
    if [[ ! -f "$JARVIS_KEYCHAIN" ]]; then
        echo "Choose a password for the Jarvis signing keychain (asked again on each build)." >&2
        /usr/bin/security create-keychain "$JARVIS_KEYCHAIN"
    fi
    /usr/bin/security unlock-keychain "$JARVIS_KEYCHAIN"
    /usr/bin/security set-keychain-settings -l -u -t 300 "$JARVIS_KEYCHAIN"
    local listed
    listed="$(/usr/bin/security list-keychains -d user | /usr/bin/sed -E 's/^[[:space:]]*"(.*)"$/\1/')"
    if ! /usr/bin/grep -Fxq "$JARVIS_KEYCHAIN" <<<"$listed"; then
        local keychains=()
        while IFS= read -r keychain; do
            [[ -n "$keychain" ]] && keychains+=("$keychain")
        done <<<"$listed"
        /usr/bin/security list-keychains -d user -s "${keychains[@]}" "$JARVIS_KEYCHAIN"
    fi
}

status_identity() {
    local hash legacy
    hash="$(identity_hash)"
    legacy="$(legacy_identity_hash)"
    if [[ -z "$hash" && -n "$legacy" ]]; then
        echo "warning=identity_in_login_keychain run=\"$0 migrate\"" >&2
        echo "status=ok identity=\"$JARVIS_IDENTITY_NAME\" sha1=$legacy keychain=login protected=false"
        return
    fi
    if [[ -z "$hash" ]]; then
        echo "status=missing identity=\"$JARVIS_IDENTITY_NAME\""
        return 1
    fi
    echo "status=ok identity=\"$JARVIS_IDENTITY_NAME\" sha1=$hash keychain=signing protected=true"
}

install_identity() {
    local existing_hash
    existing_hash="$(identity_hash)"
    if [[ -n "$existing_hash" ]]; then
        echo "status=ok identity=\"$JARVIS_IDENTITY_NAME\" sha1=$existing_hash"
        return
    fi
    if [[ -n "$(legacy_identity_hash)" && "${JARVIS_MIGRATING:-0}" != "1" ]]; then
        echo "status=error reason=identity_in_login_keychain run=\"$0 migrate\"" >&2
        return 1
    fi
    ensure_signing_keychain
    if /usr/bin/security find-certificate \
        -c "$JARVIS_IDENTITY_NAME" \
        "$JARVIS_KEYCHAIN" >/dev/null 2>&1
    then
        echo "status=error reason=conflicting_certificate" >&2
        return 1
    fi

    umask 077
    JARVIS_TEMPORARY="$(/usr/bin/mktemp -d /private/tmp/jarvis-codesign.XXXXXX)"
    local certificate="$JARVIS_TEMPORARY/certificate.pem"
    local private_key="$JARVIS_TEMPORARY/private-key.pem"
    local probe="$JARVIS_TEMPORARY/probe"

    /usr/bin/openssl genrsa -out "$private_key" 3072 >/dev/null 2>&1
    /usr/bin/openssl req \
        -new \
        -x509 \
        -sha256 \
        -days 3650 \
        -key "$private_key" \
        -subj "/CN=$JARVIS_IDENTITY_NAME/O=Jarvis Local" \
        -addext "basicConstraints=critical,CA:TRUE,pathlen:0" \
        -addext "keyUsage=critical,digitalSignature,keyCertSign" \
        -addext "extendedKeyUsage=codeSigning" \
        -keyout "$private_key" \
        -out "$certificate" >/dev/null 2>&1
    /usr/bin/openssl x509 -in "$certificate" -noout -checkend 31536000 >/dev/null

    /usr/bin/security import "$certificate" \
        -k "$JARVIS_KEYCHAIN" \
        -t cert \
        -f pemseq >/dev/null
    JARVIS_CERTIFICATE_IMPORTED=true
    /usr/bin/security add-trusted-cert \
        -r trustRoot \
        -p codeSign \
        -k "$JARVIS_KEYCHAIN" \
        "$certificate"
    /usr/bin/security import "$private_key" \
        -k "$JARVIS_KEYCHAIN" \
        -t priv \
        -f openssl \
        -x \
        -T /usr/bin/codesign >/dev/null

    local hash
    hash="$(identity_hash)"
    if [[ -z "$hash" ]]; then
        echo "status=error reason=identity_unavailable" >&2
        return 1
    fi
    /bin/cp /usr/bin/true "$probe"
    /usr/bin/codesign --force --sign "$hash" --timestamp=none "$probe" >/dev/null
    /usr/bin/codesign --verify --strict "$probe"

    /usr/bin/security lock-keychain "$JARVIS_KEYCHAIN"
    JARVIS_COMPLETE=true
    echo "status=ok identity=\"$JARVIS_IDENTITY_NAME\" sha1=$hash"
}

migrate_identity() {
    local legacy
    legacy="$(legacy_identity_hash)"
    if [[ -z "$legacy" ]]; then
        install_identity
        return
    fi
    # The old private key was imported non-extractable, so a new identity replaces it.
    JARVIS_MIGRATING=1 install_identity
    /usr/bin/security delete-identity -Z "$legacy" -t "$JARVIS_LOGIN_KEYCHAIN" >/dev/null
    echo "migrated=true previous_sha1=$legacy next=\"rebuild Jarvis.app, then run: jarvis approval-signer-reset\""
}

case "$JARVIS_ACTION" in
    install)
        install_identity
        ;;
    migrate)
        migrate_identity
        ;;
    status)
        status_identity
        ;;
    *)
        echo "usage: $0 [install|migrate|status]" >&2
        exit 2
        ;;
esac
