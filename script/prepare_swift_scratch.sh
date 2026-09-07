#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "Usage: $0 /private/tmp/aegis-<scratch>" >&2
    exit 64
fi

AEGIS_SCRATCH_DIR="$1"
case "$AEGIS_SCRATCH_DIR" in
    /private/tmp/aegis-*)
        ;;
    *)
        echo "Refusing automatic SwiftPM repair outside /private/tmp/aegis-*" >&2
        exit 64
        ;;
esac
if [[ -L "$AEGIS_SCRATCH_DIR" ]]; then
    echo "Refusing symlinked SwiftPM scratch root" >&2
    exit 1
fi

AEGIS_CHECKOUTS_DIR="$AEGIS_SCRATCH_DIR/checkouts"
AEGIS_REPOSITORIES_DIR="$AEGIS_SCRATCH_DIR/repositories"
AEGIS_WORKSPACE_STATE="$AEGIS_SCRATCH_DIR/workspace-state.json"
AEGIS_REPAIR_REQUIRED=0

if [[ -d "$AEGIS_CHECKOUTS_DIR" ]]; then
    for AEGIS_CHECKOUT in "$AEGIS_CHECKOUTS_DIR"/*; do
        [[ -e "$AEGIS_CHECKOUT" ]] || continue
        if [[
            ! -d "$AEGIS_CHECKOUT"
            || -L "$AEGIS_CHECKOUT"
            || ! -f "$AEGIS_CHECKOUT/Package.swift"
        ]]; then
            AEGIS_REPAIR_REQUIRED=1
            break
        fi
    done
fi

if [[ "$AEGIS_REPAIR_REQUIRED" == "1" ]]; then
    echo "[aegis-swiftpm] Recovering an incomplete temporary dependency checkout"
    /bin/rm -rf -- "$AEGIS_CHECKOUTS_DIR" "$AEGIS_REPOSITORIES_DIR"
    /bin/rm -f -- "$AEGIS_WORKSPACE_STATE"
fi

/bin/mkdir -p "$AEGIS_SCRATCH_DIR"
