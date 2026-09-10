#!/usr/bin/env bash
set -euo pipefail
umask 077

AEGIS_PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
AEGIS_INSTALLED_APP="$HOME/Applications/Jarvis.app"
AEGIS_INSTALLED_INFO="$AEGIS_INSTALLED_APP/Contents/Info.plist"
AEGIS_REPORT="$AEGIS_PROJECT_ROOT/dist/Jarvis.rc3-rc8.json"
AEGIS_MAX_BUNDLE_KIB="${AEGIS_MAX_BUNDLE_KIB:-450560}"
AEGIS_MAX_SWIFT_EXECUTABLE_BYTES="${AEGIS_MAX_SWIFT_EXECUTABLE_BYTES:-25165824}"
AEGIS_TEMPORARY_REPORT=""

cleanup() {
    if [[ -n "$AEGIS_TEMPORARY_REPORT" && -f "$AEGIS_TEMPORARY_REPORT" ]]; then
        /bin/rm -f -- "$AEGIS_TEMPORARY_REPORT"
    fi
}
trap cleanup EXIT INT TERM HUP

fail() {
    echo "status=error reason=$1" >&2
    exit 1
}

stage() {
    local block="$1"
    local description="$2"
    shift 2
    echo "[aegis-rc] $block · $description"
    "$@"
    echo "[aegis-rc] $block · status=passed"
}

wait_for_installed_runtime() {
    # Installing the bundle launches AppKit asynchronously. Require the same
    # authenticated health/readiness checks after startup; never infer readiness
    # merely from a live PID. This bounded retry exists only in the release gate.
    local attempt
    for attempt in 1 2 3 4 5; do
        if ./script/jarvis_beta.sh check; then
            return 0
        fi
        if (( attempt < 5 )); then
            sleep 1
        fi
    done
    fail "installed_runtime_did_not_become_ready"
}

validate_release_bundle() {
    if [[ -L "$AEGIS_INSTALLED_APP" || ! -f "$AEGIS_INSTALLED_INFO" ]]; then
        fail "installed_bundle_missing_or_unsafe"
    fi
    local installed_revision
    installed_revision="$({
        /usr/bin/plutil -extract AegisBuildRevision raw "$AEGIS_INSTALLED_INFO"
    } 2>/dev/null || true)"
    if [[ "$installed_revision" != "$AEGIS_SOURCE_REVISION" ]]; then
        fail "installed_app_revision_mismatch"
    fi
    /usr/bin/codesign --verify --deep --strict "$AEGIS_INSTALLED_APP"

    local bundle_kib
    bundle_kib="$(/usr/bin/du -sk "$AEGIS_INSTALLED_APP" | /usr/bin/awk '{print $1}')"
    if [[ ! "$bundle_kib" =~ ^[1-9][0-9]*$ ]] || (( bundle_kib > AEGIS_MAX_BUNDLE_KIB )); then
        fail "installed_bundle_size_budget_exceeded"
    fi

    local executable
    local executable_bytes
    while IFS= read -r -d '' executable; do
        executable_bytes="$(/usr/bin/stat -f '%z' "$executable")"
        if [[ ! "$executable_bytes" =~ ^[1-9][0-9]*$ ]] \
            || (( executable_bytes > AEGIS_MAX_SWIFT_EXECUTABLE_BYTES )); then
            fail "swift_executable_size_budget_exceeded"
        fi
    done < <(
        /usr/bin/find \
            "$AEGIS_INSTALLED_APP/Contents/MacOS" \
            "$AEGIS_INSTALLED_APP/Contents/Helpers" \
            -type f -perm -111 -print0
    )
    echo "status=ok bundle_kib=$bundle_kib budget_kib=$AEGIS_MAX_BUNDLE_KIB"
}

write_private_report() {
    /bin/mkdir -p "$AEGIS_PROJECT_ROOT/dist"
    AEGIS_TEMPORARY_REPORT="$(
        /usr/bin/mktemp "$AEGIS_PROJECT_ROOT/dist/.Jarvis.rc3-rc8.XXXXXX"
    )"
    /bin/chmod 600 "$AEGIS_TEMPORARY_REPORT"
    /usr/bin/printf '%s\n' \
        "{\"schema_version\":\"1.0\",\"profile\":\"jarvis_rc3_rc8_nonvoice\",\"build_revision\":\"$AEGIS_SOURCE_REVISION\",\"score\":100,\"gate_passed\":true,\"stages\":{\"RC3\":\"passed\",\"RC4\":\"passed\",\"RC5\":\"passed\",\"RC6\":\"passed\",\"RC7\":\"passed\",\"RC8\":\"passed\"},\"privacy\":{\"requires_owner_voice\":false,\"contains_prompt_text\":false,\"contains_transcripts\":false,\"contains_audio\":false,\"contains_images\":false}}" \
        >"$AEGIS_TEMPORARY_REPORT"
    /bin/mv -f "$AEGIS_TEMPORARY_REPORT" "$AEGIS_REPORT"
    AEGIS_TEMPORARY_REPORT=""
    /bin/chmod 600 "$AEGIS_REPORT"
}

cd "$AEGIS_PROJECT_ROOT"
if ! /usr/bin/git diff --quiet || ! /usr/bin/git diff --cached --quiet; then
    echo "status=blocked reason=tracked_source_tree_dirty" >&2
    exit 2
fi
AEGIS_SOURCE_REVISION="$(/usr/bin/git rev-parse HEAD)"
if [[ ! "$AEGIS_SOURCE_REVISION" =~ ^[0-9a-f]{40}$ ]]; then
    fail "source_revision_invalid"
fi
if [[ ! "$AEGIS_MAX_BUNDLE_KIB" =~ ^[1-9][0-9]*$ ]] \
    || [[ ! "$AEGIS_MAX_SWIFT_EXECUTABLE_BYTES" =~ ^[1-9][0-9]*$ ]]; then
    fail "release_budget_invalid"
fi

stage RC3 "Alcance 1.0 congelado" ./script/aegis.sh release-scope
stage RC4 "Bundle firmado, coherente y acotado" validate_release_bundle
stage RC5 "Runtime instalado listo" wait_for_installed_runtime
stage RC5 "Turno CLI determinista local" \
    ./script/jarvis.sh \
        --request "Hola" \
        --domain auto \
        --research-policy offline \
        --inference-policy local_only
stage RC5 "Contratos y flujos de producción" ./script/aegis.sh acceptance-benchmark
stage RC5 "Matriz funcional completa" ./script/aegis.sh production-workflows
stage RC6 "Reinicio supervisado y continuidad de seguridad" ./script/aegis.sh daemon-recovery
stage RC6 "Telemetría privada del runtime" ./script/aegis.sh self-evaluation
stage RC7 "Aplicación nativa y Chrome aislado" ./script/p9_browser_gate.sh
stage RC8 "Soak reforzado posterior a UI" \
    env AEGIS_P7_SOAK_CYCLES=200 ./script/p7_reliability_gate.sh

write_private_report
echo "status=ok blocks=RC3-RC8 score=100 owner_voice=deferred report=$AEGIS_REPORT"
