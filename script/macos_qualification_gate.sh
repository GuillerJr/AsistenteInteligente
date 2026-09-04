#!/usr/bin/env bash
set -euo pipefail

AEGIS_REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$AEGIS_REPOSITORY_ROOT"

echo "[aegis-hardware-gate] Building and launching the signed Swift application"
./script/build_and_run.sh --verify

echo "[aegis-hardware-gate] Running deterministic local contracts"
./script/aegis.sh acceptance-benchmark

echo "[aegis-hardware-gate] Running live macOS qualification"
if ! AEGIS_QUALIFICATION_OUTPUT="$(./script/aegis.sh macos-qualification)"; then
    printf '%s\n' "$AEGIS_QUALIFICATION_OUTPUT" >&2
    echo "[aegis-hardware-gate] Blocked: live qualification is unavailable" >&2
    exit 1
fi
printf '%s\n' "$AEGIS_QUALIFICATION_OUTPUT"

AEGIS_QUALIFICATION_SCORE="$({
    printf '%s\n' "$AEGIS_QUALIFICATION_OUTPUT" \
        | /usr/bin/sed -n 's/.*"score":\([0-9][0-9]*\).*/\1/p'
} | /usr/bin/tail -n 1)"
if [[
    "$AEGIS_QUALIFICATION_SCORE" != "100"
    || "$AEGIS_QUALIFICATION_OUTPUT" != *'"gate_passed":true'*
]]; then
    echo "[aegis-hardware-gate] Rejected: live macOS score must be 100" >&2
    exit 1
fi

echo "[aegis-hardware-gate] Hardware qualification passed"
