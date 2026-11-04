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
  if [[ "${JARVIS_TEST_GUARD_NATIVE_MEDIA_FRAMEWORKS:-}" == 1 ]]; then
    if [[ "${JARVIS_TEST_GUARD_PROFILE:-}" != local ||
          "${JARVIS_TEST_GUARD_SELECTION:-}" != 'NativeMedia(Executor|Diagnostics)Tests' ||
          "$#" -ne 2 || "${1:-}" != --filter ||
          "${2:-}" != 'NativeMedia(Executor|Diagnostics)Tests' ]]; then
      printf '%s\n' 'El proceso nativo exige la admisión y el único filtro exacto de medios.' >&2
      exit 2
    fi
  fi
  export JARVIS_DESKTOP_PYTHON="$JARVIS_PYTHON"
  export JARVIS_DESKTOP_SOURCE="$JARVIS_REPO_DIR/src"
  export JARVIS_DESKTOP_DEPENDENCIES="$("$JARVIS_PYTHON" -B -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
  export JARVIS_RPC_CLIENT_KEY_FILE="$JARVIS_TEST_GUARD_RUN_ROOT/synthetic-client.key"
  export JARVIS_RPC_BOOTSTRAP_PUBLIC_KEY="$("$JARVIS_PYTHON" -B -c '
import os, sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
key = Ed25519PrivateKey.generate()
fd = os.open(sys.argv[1], os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
with os.fdopen(fd, "wb") as output:
    output.write(key.private_bytes_raw())
print(key.public_key().public_bytes_raw().hex())
' "$JARVIS_RPC_CLIENT_KEY_FILE")"
  JARVIS_SDK_ARGUMENTS=()
  if [[ -n "${SDKROOT:-}" ]]; then JARVIS_SDK_ARGUMENTS+=(--sdk "$SDKROOT"); fi
  if [[ -n "${JARVIS_TEST_GUARD_TOOLCHAIN:-}" ]]; then
    JARVIS_SDK_ARGUMENTS+=(--toolchain "$JARVIS_TEST_GUARD_TOOLCHAIN")
  fi
  exec swift test --build-system native --disable-sandbox ${JARVIS_SDK_ARGUMENTS[@]+"${JARVIS_SDK_ARGUMENTS[@]}"} --package-path "$JARVIS_REPO_DIR/native/JarvisDesktop" \
    --scratch-path "$JARVIS_TEST_GUARD_RUN_ROOT/swift" \
    --cache-path "$JARVIS_TEST_GUARD_RUN_ROOT/cache" \
    --config-path "$JARVIS_TEST_GUARD_RUN_ROOT/config" \
    --security-path "$JARVIS_TEST_GUARD_RUN_ROOT/security" --arch arm64 "$@"
fi

JARVIS_PROFILE=local
JARVIS_FILTER=""
JARVIS_FILTER_COUNT=0
JARVIS_BUDGET=""
JARVIS_DEADLINE=600
JARVIS_NATIVE_MEDIA=0
JARVIS_SKIP_BUILD=0
JARVIS_SWIFT_ARGUMENTS=()
JARVIS_NATIVE_MEDIA_ARGUMENTS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --full) JARVIS_PROFILE=ci; shift ;;
    --native-media-frameworks)
      JARVIS_NATIVE_MEDIA=1; JARVIS_NATIVE_MEDIA_ARGUMENTS=(--native-media-frameworks); shift ;;
    --filter|--budget-mib|--deadline)
      if [[ $# -lt 2 ]]; then printf '%s\n' 'Falta el valor de una opción.' >&2; exit 2; fi
      case "$1" in
        --filter)
          JARVIS_FILTER="$2"; JARVIS_FILTER_COUNT=$((JARVIS_FILTER_COUNT + 1))
          JARVIS_SWIFT_ARGUMENTS+=(--filter "$2") ;;
        --budget-mib) JARVIS_BUDGET="$2" ;;
        --deadline) JARVIS_DEADLINE="$2" ;;
      esac
      shift 2 ;;
    --skip-build) JARVIS_SKIP_BUILD=1; JARVIS_SWIFT_ARGUMENTS+=(--skip-build); shift ;;
    *) printf '%s\n' 'Uso: test_desktop.sh --filter TEST --budget-mib N [--deadline S] [--native-media-frameworks], o --full en CI.' >&2; exit 2 ;;
  esac
done
if [[ "$JARVIS_NATIVE_MEDIA" == 1 ]]; then
  if [[ "$JARVIS_PROFILE" != local || "$JARVIS_FILTER_COUNT" -ne 1 ||
        "$JARVIS_SKIP_BUILD" -ne 0 ||
        "$JARVIS_FILTER" != 'NativeMedia(Executor|Diagnostics)Tests' ]]; then
    printf '%s\n' '--native-media-frameworks exige un único filtro Swift local exacto de medios.' >&2
    exit 2
  fi
fi
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
  ${JARVIS_FULL_ARGUMENTS[@]+"${JARVIS_FULL_ARGUMENTS[@]}"} \
  ${JARVIS_NATIVE_MEDIA_ARGUMENTS[@]+"${JARVIS_NATIVE_MEDIA_ARGUMENTS[@]}"} -- "$JARVIS_PYTHON" -B \
  "$JARVIS_REPO_DIR/script/test_resource_guard.py" admitted-exec --kind swift -- \
  /bin/bash "$JARVIS_REPO_DIR/script/test_desktop.sh" --guard-child ${JARVIS_SWIFT_ARGUMENTS[@]+"${JARVIS_SWIFT_ARGUMENTS[@]}"}
