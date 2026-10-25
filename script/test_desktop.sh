#!/bin/bash
set -euo pipefail
JARVIS_REPO_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
JARVIS_TEST_DIR="$(mktemp -d /private/tmp/jarvis-desktop-test.XXXXXX)"
cleanup_test_cache() {
  JARVIS_TEST_STATUS=$?
  if [[ "$JARVIS_TEST_STATUS" -eq 0 ]]; then
    rm -rf -- "$JARVIS_TEST_DIR"
  else
    printf 'Diagnóstico de pruebas conservado en %s\n' "$JARVIS_TEST_DIR" >&2
  fi
  exit "$JARVIS_TEST_STATUS"
}
trap cleanup_test_cache EXIT
export CLANG_MODULE_CACHE_PATH="$JARVIS_TEST_DIR/clang-cache"
export JARVIS_DESKTOP_PYTHON="$JARVIS_REPO_DIR/.venv/bin/python"
export JARVIS_DESKTOP_SOURCE="$JARVIS_REPO_DIR/src"
swift test --disable-sandbox --package-path "$JARVIS_REPO_DIR/native/JarvisDesktop" \
  --scratch-path "$JARVIS_TEST_DIR/swift" --cache-path "$JARVIS_TEST_DIR/cache" \
  --config-path "$JARVIS_TEST_DIR/config" --security-path "$JARVIS_TEST_DIR/security" --arch arm64
