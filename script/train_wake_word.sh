#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 ]]; then
    echo "usage: $0 [dataset-directory]" >&2
    exit 2
fi

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_PACKAGE_DIR="$AEGIS_PROJECT_ROOT/native/AegisAudio"
AEGIS_DATASET="${1:-$HOME/Library/Application Support/Aegis/WakeWordEnrollment}"
AEGIS_MODEL_DIR="$HOME/Library/Application Support/Aegis/Models"
AEGIS_MODEL="$AEGIS_MODEL_DIR/JarvisWakeWord.mlmodelc"
AEGIS_SCRATCH_DIR="/private/tmp/aegis-wake-word-training"
AEGIS_SDK_PATH="${AEGIS_MACOS_SDK:-/Library/Developer/CommandLineTools/SDKs/MacOSX26.5.sdk}"

/bin/mkdir -p "$AEGIS_MODEL_DIR"
/bin/chmod 700 "$AEGIS_MODEL_DIR"

env \
    SDKROOT="$AEGIS_SDK_PATH" \
    CLANG_MODULE_CACHE_PATH="$AEGIS_SCRATCH_DIR/clang-cache" \
    SWIFTPM_MODULECACHE_OVERRIDE="$AEGIS_SCRATCH_DIR/swiftpm-cache" \
    swift run \
        --package-path "$AEGIS_PACKAGE_DIR" \
        --disable-sandbox \
        --scratch-path "$AEGIS_SCRATCH_DIR" \
        jarvis-wake-word-trainer \
        "$AEGIS_DATASET" \
        "$AEGIS_MODEL"
