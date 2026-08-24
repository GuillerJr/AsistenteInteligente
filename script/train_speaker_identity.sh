#!/usr/bin/env bash
set -euo pipefail

JARVIS_MODE="train"
if [[ "${1:-}" == "--check" || "${1:-}" == "--validate-model" ]]; then
    JARVIS_MODE="$1"
    shift
fi
if [[ $# -gt 1 ]]; then
    echo "usage: $0 [dataset-directory] | --check [dataset-directory] | --validate-model [model]" >&2
    exit 2
fi

JARVIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JARVIS_PACKAGE_DIR="$JARVIS_PROJECT_ROOT/native/AegisAudio"
JARVIS_DATASET="${1:-$HOME/Library/Application Support/Aegis/SpeakerEnrollment}"
JARVIS_MODEL_DIR="$HOME/Library/Application Support/Aegis/Models"
JARVIS_MODEL="$JARVIS_MODEL_DIR/JarvisSpeakerIdentity.mlmodelc"
JARVIS_SCRATCH_DIR="/private/tmp/jarvis-speaker-training"
JARVIS_SDK_PATH="$("$JARVIS_PROJECT_ROOT/script/resolve_macos_sdk.sh")"
JARVIS_SWIFT="$(/usr/bin/xcrun --find swift)"
if [[ ! -x "$JARVIS_SWIFT" ]]; then
    echo "Swift unavailable: $JARVIS_SWIFT" >&2
    exit 1
fi

JARVIS_ARGUMENTS=("$JARVIS_DATASET" "$JARVIS_MODEL")
if [[ "$JARVIS_MODE" == "--check" ]]; then
    JARVIS_ARGUMENTS=(--check "$JARVIS_DATASET")
elif [[ "$JARVIS_MODE" == "--validate-model" ]]; then
    JARVIS_ARGUMENTS=(--validate-model "${1:-$JARVIS_MODEL}")
else
    /bin/mkdir -p "$JARVIS_MODEL_DIR"
    /bin/chmod 700 "$JARVIS_MODEL_DIR"
fi

env \
    SDKROOT="$JARVIS_SDK_PATH" \
    CLANG_MODULE_CACHE_PATH="$JARVIS_SCRATCH_DIR/clang-cache" \
    SWIFTPM_MODULECACHE_OVERRIDE="$JARVIS_SCRATCH_DIR/swiftpm-cache" \
    "$JARVIS_SWIFT" run \
        --package-path "$JARVIS_PACKAGE_DIR" \
        --disable-sandbox \
        --scratch-path "$JARVIS_SCRATCH_DIR" \
        jarvis-speaker-trainer \
        "${JARVIS_ARGUMENTS[@]}"
