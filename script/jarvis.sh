#!/usr/bin/env bash
set -euo pipefail

AEGIS_SCRIPT_SOURCE="${BASH_SOURCE[0]}"
while [[ -L "$AEGIS_SCRIPT_SOURCE" ]]; do
    AEGIS_SCRIPT_DIRECTORY="$(cd -P "$(dirname "$AEGIS_SCRIPT_SOURCE")" && pwd)"
    AEGIS_SCRIPT_SOURCE="$(readlink "$AEGIS_SCRIPT_SOURCE")"
    if [[ "$AEGIS_SCRIPT_SOURCE" != /* ]]; then
        AEGIS_SCRIPT_SOURCE="$AEGIS_SCRIPT_DIRECTORY/$AEGIS_SCRIPT_SOURCE"
    fi
done

AEGIS_PROJECT_ROOT="$(cd "$(dirname "$AEGIS_SCRIPT_SOURCE")/.." && pwd)"
AEGIS_PYTHON="$AEGIS_PROJECT_ROOT/.venv/bin/python"
if [[ ! -x "$AEGIS_PYTHON" ]]; then
    echo "Jarvis environment is unavailable. Run: uv sync" >&2
    exit 1
fi

exec env \
    AEGIS_CLI_PROGRAM_NAME=jarvis \
    PYTHONPATH="$AEGIS_PROJECT_ROOT/src" \
    "$AEGIS_PYTHON" -m aegis_core.cli "$@"
