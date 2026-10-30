#!/bin/bash
set -euo pipefail

# Development remains explicit. A product runtime is assembled only in remote CI.
JARVIS_REPO_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
JARVIS_PYTHON="$JARVIS_REPO_DIR/.venv/bin/python"
if [[ "${1:-}" != --guard-child ]]; then
  JARVIS_PROFILE=local
  JARVIS_BUDGET=""
  JARVIS_DEADLINE=600
  JARVIS_OUTPUT_DIR=""
  JARVIS_FULL_ARGUMENTS=()
  JARVIS_PRODUCT_ARGUMENTS=()
  JARVIS_MODEL_ARGUMENTS=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --full) JARVIS_PROFILE=ci; JARVIS_FULL_ARGUMENTS=(--full); shift ;;
      --self-contained) JARVIS_PRODUCT_ARGUMENTS=(--self-contained); shift ;;
      --budget-mib|--deadline|--output)
        if [[ $# -lt 2 ]]; then printf '%s\n' 'Falta el valor de una opción.' >&2; exit 2; fi
        case "$1" in
          --budget-mib) JARVIS_BUDGET="$2" ;;
          --deadline) JARVIS_DEADLINE="$2" ;;
          --output) JARVIS_OUTPUT_DIR="$2" ;;
        esac
        shift 2 ;;
      *) printf '%s\n' 'Uso: build_desktop.sh --budget-mib N [--output /private/tmp/NUEVO], o --full [--self-contained] en CI.' >&2; exit 2 ;;
    esac
  done
  if [[ ${#JARVIS_PRODUCT_ARGUMENTS[@]} -gt 0 ]]; then
    if [[ "$JARVIS_PROFILE" != ci || -z "${JARVIS_MINILM_TEST_ASSETS:-}" ]]; then
      printf '%s\n' 'El runtime autocontenido exige CI remota y el modelo público fijado.' >&2; exit 2
    fi
    JARVIS_MODEL_ARGUMENTS=(--minilm-test-assets "$JARVIS_MINILM_TEST_ASSETS")
  fi
  if [[ "$JARVIS_PROFILE" == local && -z "$JARVIS_BUDGET" ]]; then
    printf '%s\n' 'La compilación local exige --budget-mib (máximo 2048); no se amplía automáticamente.' >&2
    exit 2
  fi
  JARVIS_BUDGET="${JARVIS_BUDGET:-6144}"
  JARVIS_EXPORT_ARGUMENTS=()
  if [[ -n "$JARVIS_OUTPUT_DIR" ]]; then
    JARVIS_EXPORT_ARGUMENTS=(--export-relative output/Jarvis.app --export-destination "$JARVIS_OUTPUT_DIR")
    if [[ ${#JARVIS_PRODUCT_ARGUMENTS[@]} -gt 0 ]]; then
      JARVIS_EXPORT_ARGUMENTS+=(--self-contained-export)
    fi
  fi
  exec "$JARVIS_PYTHON" -B "$JARVIS_REPO_DIR/script/test_resource_guard.py" run \
    --repo-root "$JARVIS_REPO_DIR" --profile "$JARVIS_PROFILE" --kind build --selection Jarvis \
    --budget-mib "$JARVIS_BUDGET" --deadline "$JARVIS_DEADLINE" \
    ${JARVIS_FULL_ARGUMENTS[@]+"${JARVIS_FULL_ARGUMENTS[@]}"} ${JARVIS_EXPORT_ARGUMENTS[@]+"${JARVIS_EXPORT_ARGUMENTS[@]}"} \
    ${JARVIS_MODEL_ARGUMENTS[@]+"${JARVIS_MODEL_ARGUMENTS[@]}"} -- \
    "$JARVIS_PYTHON" -B "$JARVIS_REPO_DIR/script/test_resource_guard.py" \
    admitted-exec --kind build -- /bin/bash "$JARVIS_REPO_DIR/script/build_desktop.sh" --guard-child \
    ${JARVIS_PRODUCT_ARGUMENTS[@]+"${JARVIS_PRODUCT_ARGUMENTS[@]}"}
fi
if [[ -z "${JARVIS_TEST_GUARD_RUN_ROOT:-}" || -z "${JARVIS_TEST_GUARD_ADMISSION:-}" ]]; then
  printf '%s\n' 'Falta la admisión del supervisor de compilación.' >&2; exit 2
fi
if [[ "${JARVIS_TEST_GUARD_CHILD_CHECKED:-}" != "${JARVIS_TEST_GUARD_NONCE:-missing}" ]]; then
  printf '%s\n' 'El supervisor no admitió este proceso de compilación.' >&2; exit 2
fi
JARVIS_BUILD_DIR="$JARVIS_TEST_GUARD_RUN_ROOT"
JARVIS_OUTPUT_DIR="$JARVIS_BUILD_DIR/output"
JARVIS_APP_PATH="$JARVIS_OUTPUT_DIR/Jarvis.app"
export CLANG_MODULE_CACHE_PATH="$JARVIS_BUILD_DIR/clang-cache"
JARVIS_SDK_ARGUMENTS=()
if [[ -n "${SDKROOT:-}" ]]; then JARVIS_SDK_ARGUMENTS+=(--sdk "$SDKROOT"); fi
if [[ -n "${JARVIS_TEST_GUARD_TOOLCHAIN:-}" ]]; then
  JARVIS_SDK_ARGUMENTS+=(--toolchain "$JARVIS_TEST_GUARD_TOOLCHAIN")
fi
swift build --build-system native --disable-sandbox ${JARVIS_SDK_ARGUMENTS[@]+"${JARVIS_SDK_ARGUMENTS[@]}"} --package-path "$JARVIS_REPO_DIR/native/JarvisDesktop" \
  --scratch-path "$JARVIS_BUILD_DIR/swift" --cache-path "$JARVIS_BUILD_DIR/cache" \
  --config-path "$JARVIS_BUILD_DIR/config" --security-path "$JARVIS_BUILD_DIR/security" \
  --arch arm64 --product Jarvis
if [[ "${JARVIS_BUILD_QA_LAUNCHER:-0}" == 1 ]]; then
  swift build --build-system native --disable-sandbox ${JARVIS_SDK_ARGUMENTS[@]+"${JARVIS_SDK_ARGUMENTS[@]}"} --package-path "$JARVIS_REPO_DIR/native/JarvisDesktop" \
    --scratch-path "$JARVIS_BUILD_DIR/swift" --cache-path "$JARVIS_BUILD_DIR/cache" \
    --config-path "$JARVIS_BUILD_DIR/config" --security-path "$JARVIS_BUILD_DIR/security" \
    --arch arm64 --product JarvisQALauncher
fi
if [[ "${2:-}" == --self-contained ]]; then
  swift build --build-system native --disable-sandbox ${JARVIS_SDK_ARGUMENTS[@]+"${JARVIS_SDK_ARGUMENTS[@]}"} --package-path "$JARVIS_REPO_DIR/native/JarvisDesktop" \
    --scratch-path "$JARVIS_BUILD_DIR/swift" --cache-path "$JARVIS_BUILD_DIR/cache" \
    --config-path "$JARVIS_BUILD_DIR/config" --security-path "$JARVIS_BUILD_DIR/security" \
    --arch arm64 --product JarvisRuntimeCheck
fi
JARVIS_BIN_DIR="$(swift build --build-system native --disable-sandbox ${JARVIS_SDK_ARGUMENTS[@]+"${JARVIS_SDK_ARGUMENTS[@]}"} --package-path "$JARVIS_REPO_DIR/native/JarvisDesktop" \
  --scratch-path "$JARVIS_BUILD_DIR/swift" --cache-path "$JARVIS_BUILD_DIR/cache" \
  --config-path "$JARVIS_BUILD_DIR/config" --security-path "$JARVIS_BUILD_DIR/security" \
  --arch arm64 --show-bin-path)"
mkdir -p "$JARVIS_APP_PATH/Contents/MacOS"
cp "$JARVIS_BIN_DIR/Jarvis" "$JARVIS_APP_PATH/Contents/MacOS/Jarvis"
if [[ "${JARVIS_BUILD_QA_LAUNCHER:-0}" == 1 ]]; then
  mkdir -p "$JARVIS_APP_PATH/Contents/Helpers"
  cp "$JARVIS_BIN_DIR/JarvisQALauncher" "$JARVIS_APP_PATH/Contents/Helpers/JarvisQALauncher"
  /usr/bin/codesign --force --sign - --timestamp=none "$JARVIS_APP_PATH/Contents/Helpers/JarvisQALauncher"
  /usr/bin/codesign --verify --strict "$JARVIS_APP_PATH/Contents/Helpers/JarvisQALauncher"
fi
"$JARVIS_PYTHON" -B -c '
import plistlib, sys
data = {
    "CFBundleIdentifier": "com.jarvis.desktop.baseline",
    "CFBundleName": "Jarvis", "CFBundleDisplayName": "Jarvis",
    "CFBundleExecutable": "Jarvis", "CFBundlePackageType": "APPL",
    "CFBundleShortVersionString": "0.1.0", "CFBundleVersion": "1",
    "LSMinimumSystemVersion": "14.0", "NSPrincipalClass": "NSApplication",
    "LSUIElement": False, "NSHighResolutionCapable": True,
    "NSMicrophoneUsageDescription": "Jarvis usa el micrófono solo cuando inicias el dictado. El audio permanece en memoria y se exige reconocimiento en este Mac.",
    "NSSpeechRecognitionUsageDescription": "Jarvis convierte tu voz a texto únicamente al iniciar el dictado y si el reconocimiento local está disponible.",
}
with open(sys.argv[1], "wb") as stream:
    plistlib.dump(data, stream)
' "$JARVIS_APP_PATH/Contents/Info.plist"
/usr/bin/plutil -lint "$JARVIS_APP_PATH/Contents/Info.plist"
if [[ "${2:-}" == --self-contained ]]; then
  "$JARVIS_PYTHON" -B "$JARVIS_REPO_DIR/script/package_desktop_runtime.py" \
    --app "$JARVIS_APP_PATH" --scratch "$JARVIS_BUILD_DIR/runtime-acquisition" \
    --source-commit "$GITHUB_SHA" --model-assets "$JARVIS_MINILM_TEST_ASSETS" --identity -
  JARVIS_RUNTIME_PIN="$("$JARVIS_PYTHON" -B -c 'import plistlib,sys; print(plistlib.load(open(sys.argv[1],"rb"))["JarvisRuntimeManifestSHA256"])' "$JARVIS_APP_PATH/Contents/Info.plist")"
  "$JARVIS_BIN_DIR/JarvisRuntimeCheck" \
    --root "$JARVIS_APP_PATH/Contents/Resources/JarvisRuntime" \
    --manifest-sha256 "$JARVIS_RUNTIME_PIN" --source-commit "$GITHUB_SHA" \
    > "$JARVIS_APP_PATH/Contents/Resources/JarvisNativeRuntimeCheck.json"
  "$JARVIS_PYTHON" -B "$JARVIS_REPO_DIR/script/qualify_bundled_runtime.py" \
    --app "$JARVIS_APP_PATH" --work "$JARVIS_BUILD_DIR/relocation-qualification" \
    --source-commit "$GITHUB_SHA" \
    --receipt "$JARVIS_APP_PATH/Contents/Resources/JarvisRuntimeQualification.json"
fi
/usr/bin/codesign --force --sign - --timestamp=none "$JARVIS_APP_PATH"
/usr/bin/codesign --verify --strict "$JARVIS_APP_PATH"
if [[ "${2:-}" == --self-contained ]]; then
  printf '%s\n' 'Bundle autocontenido verificado dentro del presupuesto; firma local de CI, sin notarización.'
else
  printf '%s\n' 'Bundle de desarrollo verificado dentro del presupuesto. Su exportación requiere --output explícito.'
fi
