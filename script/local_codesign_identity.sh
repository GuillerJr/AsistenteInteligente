#!/usr/bin/env bash
set -euo pipefail

JARVIS_ACTION="${1:-status}"
JARVIS_IDENTITY_NAME="Jarvis Local Development"
JARVIS_KEYCHAIN_OUTPUT="$(/usr/bin/security default-keychain -d user)"
JARVIS_KEYCHAIN="${JARVIS_KEYCHAIN_OUTPUT#*\"}"
JARVIS_KEYCHAIN="${JARVIS_KEYCHAIN%\"*}"
JARVIS_TEMPORARY=""
JARVIS_CERTIFICATE_IMPORTED=false
JARVIS_COMPLETE=false

if [[ -z "$JARVIS_KEYCHAIN" || ! -f "$JARVIS_KEYCHAIN" || -L "$JARVIS_KEYCHAIN" ]]; then
    echo "status=error reason=unsafe_default_keychain" >&2
    exit 1
fi

identity_hash() {
    /usr/bin/security find-identity -v -p codesigning "$JARVIS_KEYCHAIN" 2>/dev/null \
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

status_identity() {
    local hash
    hash="$(identity_hash)"
    if [[ -z "$hash" ]]; then
        echo "status=missing identity=\"$JARVIS_IDENTITY_NAME\""
        return 1
    fi
    echo "status=ok identity=\"$JARVIS_IDENTITY_NAME\" sha1=$hash"
}

install_identity() {
    local existing_hash
    existing_hash="$(identity_hash)"
    if [[ -n "$existing_hash" ]]; then
        echo "status=ok identity=\"$JARVIS_IDENTITY_NAME\" sha1=$existing_hash"
        return
    fi
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

    JARVIS_COMPLETE=true
    echo "status=ok identity=\"$JARVIS_IDENTITY_NAME\" sha1=$hash"
}

case "$JARVIS_ACTION" in
    install)
        install_identity
        ;;
    status)
        status_identity
        ;;
    *)
        echo "usage: $0 [install|status]" >&2
        exit 2
        ;;
esac
