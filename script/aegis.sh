#!/usr/bin/env bash
set -euo pipefail

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_PYTHON="$AEGIS_PROJECT_ROOT/.venv/bin/python"
test -x "$AEGIS_PYTHON"
exec env PYTHONPATH="$AEGIS_PROJECT_ROOT/src" "$AEGIS_PYTHON" -m aegis_core.cli "$@"
