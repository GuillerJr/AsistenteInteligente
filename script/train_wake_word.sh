#!/usr/bin/env bash
set -euo pipefail

AEGIS_MODE="train"
if [[ "${1:-}" == "--check" || "${1:-}" == "--validate-model" ]]; then
    AEGIS_MODE="$1"
    shift
fi
if [[ $# -gt 1 ]]; then
    echo "usage: $0 [dataset-directory] | --check [dataset-directory] | --validate-model [model]" >&2
    exit 2
fi

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_PACKAGE_DIR="$AEGIS_PROJECT_ROOT/native/AegisAudio"
AEGIS_DATASET="${1:-$HOME/Library/Application Support/Aegis/WakeWordEnrollment}"
AEGIS_MODEL_DIR="$HOME/Library/Application Support/Aegis/Models"
AEGIS_MODEL="$AEGIS_MODEL_DIR/JarvisWakeWord.mlmodelc"
AEGIS_SCRATCH_DIR="/private/tmp/aegis-wake-word-training"
AEGIS_SDK_PATH="$("$AEGIS_PROJECT_ROOT/script/resolve_macos_sdk.sh")"
AEGIS_SWIFT="$(/usr/bin/xcrun --find swift)"
if [[ ! -x "$AEGIS_SWIFT" ]]; then
    echo "Swift unavailable: $AEGIS_SWIFT" >&2
    exit 1
fi

AEGIS_ARGUMENTS=("$AEGIS_DATASET" "$AEGIS_MODEL")
if [[ "$AEGIS_MODE" == "--check" ]]; then
    AEGIS_ARGUMENTS=(--check "$AEGIS_DATASET")
elif [[ "$AEGIS_MODE" == "--validate-model" ]]; then
    AEGIS_ARGUMENTS=(--validate-model "${1:-$AEGIS_MODEL}")
else
    /bin/mkdir -p "$AEGIS_MODEL_DIR"
    /bin/chmod 700 "$AEGIS_MODEL_DIR"
fi

env \
    SDKROOT="$AEGIS_SDK_PATH" \
    CLANG_MODULE_CACHE_PATH="$AEGIS_SCRATCH_DIR/clang-cache" \
    SWIFTPM_MODULECACHE_OVERRIDE="$AEGIS_SCRATCH_DIR/swiftpm-cache" \
    "$AEGIS_SWIFT" run \
        --package-path "$AEGIS_PACKAGE_DIR" \
        --disable-sandbox \
        --scratch-path "$AEGIS_SCRATCH_DIR" \
        jarvis-wake-word-trainer \
        "${AEGIS_ARGUMENTS[@]}"
