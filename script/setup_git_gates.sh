#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 [--install|--check]" >&2
}

if [[ $# -gt 1 ]]; then
    usage
    exit 64
fi

AEGIS_OPERATION="${1:---install}"
if [[ "$AEGIS_OPERATION" != "--install" && "$AEGIS_OPERATION" != "--check" ]]; then
    usage
    exit 64
fi

AEGIS_REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$AEGIS_REPOSITORY_ROOT"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "Aegis Git gate must run inside a Git worktree" >&2
    exit 1
fi

if [[ "$(git rev-parse --show-toplevel)" != "$AEGIS_REPOSITORY_ROOT" ]]; then
    echo "Aegis Git gate repository root is inconsistent" >&2
    exit 1
fi

if [[ ! -x .githooks/pre-commit || ! -x .githooks/pre-push ]]; then
    echo "Aegis Git gate files are missing or not executable" >&2
    exit 1
fi

AEGIS_CURRENT_HOOKS_PATH="$(git config --local --get core.hooksPath 2>/dev/null || true)"
if [[ "$AEGIS_OPERATION" == "--check" ]]; then
    if [[ "$AEGIS_CURRENT_HOOKS_PATH" != ".githooks" ]]; then
        echo "status=inactive hooks_path=${AEGIS_CURRENT_HOOKS_PATH:-unset}"
        exit 2
    fi
    echo "status=active hooks_path=.githooks pre_commit=fast pre_push=full hardware=manual"
    exit 0
fi

git config --local core.hooksPath .githooks
if [[ "$(git config --local --get core.hooksPath)" != ".githooks" ]]; then
    echo "Unable to activate the Aegis Git hooks path" >&2
    exit 1
fi

echo "status=active hooks_path=.githooks pre_commit=fast pre_push=full hardware=manual"
