#!/usr/bin/env bash
set -euo pipefail

AEGIS_REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$AEGIS_REPOSITORY_ROOT"

if [[ ! -f .git/HEAD || ! -x .githooks/pre-commit ]]; then
    echo "Aegis Git gate files are missing or not executable" >&2
    exit 1
fi

git config core.hooksPath .githooks
if [[ "$(git config --get core.hooksPath)" != ".githooks" ]]; then
    echo "Unable to activate the Aegis Git hooks path" >&2
    exit 1
fi

echo "Aegis pre-commit qualification gate activated"
