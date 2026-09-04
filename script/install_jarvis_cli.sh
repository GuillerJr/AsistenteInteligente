#!/usr/bin/env bash
set -euo pipefail

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_USER_BIN="$HOME/.local/bin"
AEGIS_EXECUTABLE="$AEGIS_USER_BIN/jarvis"
AEGIS_LAUNCHER="$AEGIS_PROJECT_ROOT/script/jarvis.sh"

if [[ ! -x "$AEGIS_LAUNCHER" ]]; then
    echo "Jarvis launcher is unavailable or not executable" >&2
    exit 1
fi

mkdir -p "$AEGIS_USER_BIN"
ln -sfn "$AEGIS_LAUNCHER" "$AEGIS_EXECUTABLE"

if [[ ! -x "$AEGIS_EXECUTABLE" ]]; then
    echo "Jarvis CLI installation did not produce an executable" >&2
    exit 1
fi

"$AEGIS_EXECUTABLE" --help >/dev/null
if [[ ":$PATH:" != *":$AEGIS_USER_BIN:"* ]]; then
    echo "status=ok executable=$AEGIS_EXECUTABLE"
    echo "Add $AEGIS_USER_BIN to PATH once, then open a new Terminal:" >&2
    echo '  echo '\''export PATH="$HOME/.local/bin:$PATH"'\'' >> "$HOME/.zprofile"' >&2
    exit 0
fi
echo "status=ok executable=$AEGIS_EXECUTABLE command=jarvis"
