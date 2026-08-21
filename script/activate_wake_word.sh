#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 ]]; then
    echo "usage: $0 [dataset-directory]" >&2
    exit 2
fi

JARVIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
JARVIS_DATASET="${1:-$HOME/Library/Application Support/Aegis/WakeWordEnrollment}"
JARVIS_MODEL="$HOME/Library/Application Support/Aegis/Models/JarvisWakeWord.mlmodelc"
JARVIS_INSTALLED_MODEL="$HOME/Applications/Jarvis.app/Contents/Resources/JarvisWakeWord.mlmodelc"

if [[ -e "$JARVIS_MODEL" || -L "$JARVIS_MODEL" ]]; then
    "$JARVIS_PROJECT_ROOT/script/train_wake_word.sh" --validate-model "$JARVIS_MODEL"
else
    "$JARVIS_PROJECT_ROOT/script/train_wake_word.sh" --check "$JARVIS_DATASET"
    "$JARVIS_PROJECT_ROOT/script/train_wake_word.sh" "$JARVIS_DATASET"
fi

"$JARVIS_PROJECT_ROOT/script/menu_bar_service.sh" install
"$JARVIS_PROJECT_ROOT/script/train_wake_word.sh" \
    --validate-model "$JARVIS_INSTALLED_MODEL"
"$JARVIS_PROJECT_ROOT/script/menu_bar_service.sh" status
echo "status=ok wake_word=installed"
