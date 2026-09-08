#!/usr/bin/env bash
set -euo pipefail
umask 077

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AEGIS_OUTPUT_DIRECTORY="${1:-}"
AEGIS_PYINSTALLER="$AEGIS_PROJECT_ROOT/.venv/bin/pyinstaller"
AEGIS_SPEC="$AEGIS_PROJECT_ROOT/packaging/jarvis_daemon.spec"
AEGIS_WORK_ROOT="${AEGIS_DAEMON_BUILD_ROOT:-/private/tmp/aegis-daemon-freeze}"
AEGIS_DIST_ROOT="$AEGIS_WORK_ROOT/dist"
AEGIS_BUILD_ROOT="$AEGIS_WORK_ROOT/build"

die() {
    echo "status=error reason=$1" >&2
    exit "${2:-1}"
}

if [[ -z "$AEGIS_OUTPUT_DIRECTORY" || "$AEGIS_OUTPUT_DIRECTORY" != /* ]]; then
    die "daemon_output_must_be_absolute" 2
fi
AEGIS_OUTPUT_DIRECTORY="$(
    /usr/bin/python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' \
        "$AEGIS_OUTPUT_DIRECTORY"
)"
AEGIS_WORK_ROOT="$(
    /usr/bin/python3 -c 'import os, sys; print(os.path.realpath(sys.argv[1]))' \
        "$AEGIS_WORK_ROOT"
)"
case "$AEGIS_OUTPUT_DIRECTORY" in
    /private/tmp/*) ;;
    *) die "unsafe_daemon_output" 2 ;;
esac
case "$AEGIS_WORK_ROOT" in
    /private/tmp/aegis-daemon-*) ;;
    *) die "unsafe_daemon_build_root" 2 ;;
esac
test -x "$AEGIS_PYINSTALLER" || die "pyinstaller_unavailable"
test -f "$AEGIS_SPEC" || die "daemon_spec_missing"
if [[ -L "$AEGIS_OUTPUT_DIRECTORY" ]]; then
    die "daemon_output_is_symbolic_link"
fi
if [[
    -e "$AEGIS_OUTPUT_DIRECTORY"
    && ( ! -d "$AEGIS_OUTPUT_DIRECTORY" || ! -O "$AEGIS_OUTPUT_DIRECTORY" )
]]; then
    die "unsafe_daemon_output"
fi

/bin/rm -rf -- "$AEGIS_WORK_ROOT"
/bin/mkdir -p "$AEGIS_DIST_ROOT" "$AEGIS_BUILD_ROOT"
/bin/chmod 700 "$AEGIS_WORK_ROOT" "$AEGIS_DIST_ROOT" "$AEGIS_BUILD_ROOT"

env -u PYTHONPATH \
    PYINSTALLER_CONFIG_DIR="$AEGIS_WORK_ROOT/config" \
    "$AEGIS_PYINSTALLER" \
    --noconfirm \
    --clean \
    --distpath "$AEGIS_DIST_ROOT" \
    --workpath "$AEGIS_BUILD_ROOT" \
    "$AEGIS_SPEC"

AEGIS_FROZEN_DIRECTORY="$AEGIS_DIST_ROOT/jarvis-daemon"
AEGIS_FROZEN_EXECUTABLE="$AEGIS_FROZEN_DIRECTORY/jarvis-daemon"
test -x "$AEGIS_FROZEN_EXECUTABLE" || die "frozen_daemon_missing"
/usr/bin/file "$AEGIS_FROZEN_EXECUTABLE" | /usr/bin/grep -q "arm64" \
    || die "frozen_daemon_architecture_invalid"

/bin/rm -rf -- "$AEGIS_OUTPUT_DIRECTORY"
/usr/bin/ditto --norsrc "$AEGIS_FROZEN_DIRECTORY" "$AEGIS_OUTPUT_DIRECTORY"
/bin/chmod 700 "$AEGIS_OUTPUT_DIRECTORY" "$AEGIS_OUTPUT_DIRECTORY/jarvis-daemon"

AEGIS_SELF_TEST="$AEGIS_WORK_ROOT/self-test.json"
env -i \
    HOME="$HOME" \
    LANG=C \
    LC_ALL=C \
    PATH=/usr/bin:/bin:/usr/sbin:/sbin \
    "$AEGIS_OUTPUT_DIRECTORY/jarvis-daemon" --self-test >"$AEGIS_SELF_TEST"
"$AEGIS_PROJECT_ROOT/.venv/bin/python" -c '
import json, pathlib, sys
payload = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
if payload.get("profile") != "jarvis_frozen_daemon_self_test":
    raise SystemExit(1)
if payload.get("gate_passed") is not True:
    raise SystemExit(1)
' "$AEGIS_SELF_TEST" || die "frozen_daemon_self_test_failed"

echo "status=ok artifact=$AEGIS_OUTPUT_DIRECTORY profile=self-contained-arm64"
