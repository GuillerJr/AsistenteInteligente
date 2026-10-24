#!/usr/bin/env bash
set -euo pipefail

if [[ $# -gt 1 || ( ${1:---install} != --install && ${1:---install} != --check ) ]]; then
    echo "Uso: $0 [--install|--check]" >&2
    exit 64
fi
AEGIS_OPERATION="${1:---install}"
AEGIS_REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$AEGIS_REPOSITORY_ROOT"
if [[ "$(git rev-parse --show-toplevel)" != "$AEGIS_REPOSITORY_ROOT" ]]; then
    echo "Raíz Git inconsistente." >&2
    exit 1
fi
if [[ ! -x .githooks/pre-commit || ! -x .githooks/pre-push ]]; then
    echo "Faltan hooks ejecutables." >&2
    exit 1
fi
AEGIS_CURRENT_HOOKS_PATH="$(git config --local --get core.hooksPath 2>/dev/null || true)"
if [[ "$AEGIS_OPERATION" == --check ]]; then
    if [[ "$AEGIS_CURRENT_HOOKS_PATH" != .githooks ]]; then
        echo "status=inactive hooks_path=${AEGIS_CURRENT_HOOKS_PATH:-unset}"
        exit 2
    fi
else
    git config --local core.hooksPath .githooks
    if [[ "$(git config --local --get core.hooksPath)" != .githooks ]]; then
        echo "No se pudieron activar los hooks." >&2
        exit 1
    fi
fi
echo "status=active hooks_path=.githooks profile=jarvis_base pre_commit=python pre_push=python_package native=pending"
