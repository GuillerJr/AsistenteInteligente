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
export JARVIS_DESKTOP_DEPENDENCIES="$($JARVIS_DESKTOP_PYTHON -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
export JARVIS_RPC_CLIENT_KEY_FILE="$JARVIS_TEST_DIR/synthetic-client.key"
export JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY="$($JARVIS_DESKTOP_PYTHON - "$JARVIS_RPC_CLIENT_KEY_FILE" <<'PY'
import os, sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key = Ed25519PrivateKey.generate()
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'wb') as output:
    output.write(key.private_bytes_raw())
print(key.public_key().public_bytes_raw().hex())
PY
)"
swift test --disable-sandbox --package-path "$JARVIS_REPO_DIR/native/JarvisDesktop" \
  --scratch-path "$JARVIS_TEST_DIR/swift" --cache-path "$JARVIS_TEST_DIR/cache" \
  --config-path "$JARVIS_TEST_DIR/config" --security-path "$JARVIS_TEST_DIR/security" --arch arm64 "$@"
