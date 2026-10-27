#!/bin/bash
set -euo pipefail
JARVIS_REPO_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
JARVIS_PYTHON="$JARVIS_REPO_DIR/.venv/bin/python"

if [[ "${1:-}" == --guard-child ]]; then
  shift
  if [[ -z "${JARVIS_TEST_GUARD_RUN_ROOT:-}" || -z "${JARVIS_TEST_GUARD_ADMISSION:-}" ]]; then
    printf '%s\n' 'Falta la admisión del supervisor de pruebas.' >&2
    exit 2
  fi
  if [[ "${JARVIS_TEST_GUARD_CHILD_CHECKED:-}" != "${JARVIS_TEST_GUARD_NONCE:-missing}" ]]; then
    printf '%s\n' 'El supervisor no admitió este proceso de pruebas.' >&2; exit 2
  fi
  export JARVIS_DESKTOP_PYTHON="$JARVIS_PYTHON"
  export JARVIS_DESKTOP_SOURCE="$JARVIS_REPO_DIR/src"
  export JARVIS_DESKTOP_DEPENDENCIES="$("$JARVIS_PYTHON" -B -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
  export JARVIS_RPC_CLIENT_KEY_FILE="$JARVIS_TEST_GUARD_RUN_ROOT/synthetic-client.key"
  export JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY="$("$JARVIS_PYTHON" -B - "$JARVIS_RPC_CLIENT_KEY_FILE" <<'PY'
import os, sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key = Ed25519PrivateKey.generate()
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, 'wb') as output:
    output.write(key.private_bytes_raw())
print(key.public_key().public_bytes_raw().hex())
PY
)"
  exec swift test --disable-sandbox --package-path "$JARVIS_REPO_DIR/native/JarvisDesktop" \
    --scratch-path "$JARVIS_TEST_GUARD_RUN_ROOT/swift" \
    --cache-path "$JARVIS_TEST_GUARD_RUN_ROOT/cache" \
    --config-path "$JARVIS_TEST_GUARD_RUN_ROOT/config" \
    --security-path "$JARVIS_TEST_GUARD_RUN_ROOT/security" --arch arm64 "$@"
fi

JARVIS_PROFILE=local
JARVIS_FILTER=""
JARVIS_BUDGET=""
JARVIS_DEADLINE=600
JARVIS_SWIFT_ARGUMENTS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) JARVIS_PROFILE=ci; shift ;;
    --filter|--budget-mib|--deadline)
      if [[ $# -lt 2 ]]; then printf '%s\n' 'Falta el valor de una opción.' >&2; exit 2; fi
      case "$1" in
        --filter) JARVIS_FILTER="$2"; JARVIS_SWIFT_ARGUMENTS+=(--filter "$2") ;;
        --budget-mib) JARVIS_BUDGET="$2" ;;
        --deadline) JARVIS_DEADLINE="$2" ;;
      esac
      shift 2 ;;
    --skip-build) JARVIS_SWIFT_ARGUMENTS+=(--skip-build); shift ;;
    *) printf '%s\n' 'Uso: test_desktop.sh --filter TEST --budget-mib N [--deadline S], o --full en CI.' >&2; exit 2 ;;
  esac
done
JARVIS_FULL_ARGUMENTS=()
if [[ "$JARVIS_PROFILE" == local ]]; then
  if [[ -z "$JARVIS_FILTER" || -z "$JARVIS_BUDGET" ]]; then
    printf '%s\n' 'Las pruebas locales exigen --filter y --budget-mib (máximo 2048). La suite completa se ejecuta en CI.' >&2
    exit 2
  fi
  JARVIS_SELECTION="$JARVIS_FILTER"
else
  if [[ -n "$JARVIS_FILTER" ]]; then printf '%s\n' '--full no admite un filtro.' >&2; exit 2; fi
  JARVIS_SELECTION=tests
  JARVIS_BUDGET="${JARVIS_BUDGET:-6144}"
  JARVIS_FULL_ARGUMENTS=(--full)
fi
exec "$JARVIS_PYTHON" -B "$JARVIS_REPO_DIR/script/test_resource_guard.py" run \
  --repo-root "$JARVIS_REPO_DIR" --profile "$JARVIS_PROFILE" --kind swift \
  --selection "$JARVIS_SELECTION" --budget-mib "$JARVIS_BUDGET" --deadline "$JARVIS_DEADLINE" \
  "${JARVIS_FULL_ARGUMENTS[@]}" -- "$JARVIS_PYTHON" -B \
  "$JARVIS_REPO_DIR/script/test_resource_guard.py" admitted-exec --kind swift -- \
  /bin/bash "$JARVIS_REPO_DIR/script/test_desktop.sh" --guard-child "${JARVIS_SWIFT_ARGUMENTS[@]}"
