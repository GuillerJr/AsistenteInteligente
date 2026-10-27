#!/bin/bash
set -euo pipefail

# Development bundle only: external Python source and Codex are explicit launch flags.
JARVIS_REPO_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
JARVIS_PYTHON="$JARVIS_REPO_DIR/.venv/bin/python"
if [[ "${1:-}" != --guard-child ]]; then
  JARVIS_PROFILE=local
  JARVIS_BUDGET=""
  JARVIS_DEADLINE=600
  JARVIS_OUTPUT_DIR=""
  JARVIS_FULL_ARGUMENTS=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --full) JARVIS_PROFILE=ci; JARVIS_FULL_ARGUMENTS=(--full); shift ;;
      --budget-mib|--deadline|--output)
        if [[ $# -lt 2 ]]; then printf '%s\n' 'Falta el valor de una opción.' >&2; exit 2; fi
        case "$1" in
          --budget-mib) JARVIS_BUDGET="$2" ;;
          --deadline) JARVIS_DEADLINE="$2" ;;
          --output) JARVIS_OUTPUT_DIR="$2" ;;
        esac
        shift 2 ;;
      *) printf '%s\n' 'Uso: build_desktop.sh --budget-mib N [--output /private/tmp/NUEVO], o --full en CI.' >&2; exit 2 ;;
    esac
  done
  if [[ "$JARVIS_PROFILE" == local && -z "$JARVIS_BUDGET" ]]; then
    printf '%s\n' 'La compilación local exige --budget-mib (máximo 2048); no se amplía automáticamente.' >&2
    exit 2
  fi
  JARVIS_BUDGET="${JARVIS_BUDGET:-6144}"
  JARVIS_EXPORT_ARGUMENTS=()
  if [[ -n "$JARVIS_OUTPUT_DIR" ]]; then
    JARVIS_EXPORT_ARGUMENTS=(--export-relative output/Jarvis.app --export-destination "$JARVIS_OUTPUT_DIR")
  fi
  exec "$JARVIS_PYTHON" -B "$JARVIS_REPO_DIR/script/test_resource_guard.py" run \
    --repo-root "$JARVIS_REPO_DIR" --profile "$JARVIS_PROFILE" --kind build --selection Jarvis \
    --budget-mib "$JARVIS_BUDGET" --deadline "$JARVIS_DEADLINE" \
    ${JARVIS_FULL_ARGUMENTS[@]+"${JARVIS_FULL_ARGUMENTS[@]}"} ${JARVIS_EXPORT_ARGUMENTS[@]+"${JARVIS_EXPORT_ARGUMENTS[@]}"} -- \
    "$JARVIS_PYTHON" -B "$JARVIS_REPO_DIR/script/test_resource_guard.py" \
    admitted-exec --kind build -- /bin/bash "$JARVIS_REPO_DIR/script/build_desktop.sh" --guard-child
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
swift build --disable-sandbox --package-path "$JARVIS_REPO_DIR/native/JarvisDesktop" \
  --scratch-path "$JARVIS_BUILD_DIR/swift" --cache-path "$JARVIS_BUILD_DIR/cache" \
  --config-path "$JARVIS_BUILD_DIR/config" --security-path "$JARVIS_BUILD_DIR/security" \
  --arch arm64 --product Jarvis
JARVIS_BIN_DIR="$(swift build --disable-sandbox --package-path "$JARVIS_REPO_DIR/native/JarvisDesktop" \
  --scratch-path "$JARVIS_BUILD_DIR/swift" --cache-path "$JARVIS_BUILD_DIR/cache" \
  --config-path "$JARVIS_BUILD_DIR/config" --security-path "$JARVIS_BUILD_DIR/security" \
  --arch arm64 --show-bin-path)"
mkdir -p "$JARVIS_APP_PATH/Contents/MacOS"
cp "$JARVIS_BIN_DIR/Jarvis" "$JARVIS_APP_PATH/Contents/MacOS/Jarvis"
cat > "$JARVIS_APP_PATH/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>com.jarvis.desktop.baseline</string>
<key>CFBundleName</key><string>Jarvis</string>
<key>CFBundleDisplayName</key><string>Jarvis</string>
<key>CFBundleExecutable</key><string>Jarvis</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>0.1.0</string>
<key>CFBundleVersion</key><string>1</string>
<key>LSMinimumSystemVersion</key><string>14.0</string>
<key>NSPrincipalClass</key><string>NSApplication</string>
<key>LSUIElement</key><false/>
<key>NSHighResolutionCapable</key><true/>
<key>NSMicrophoneUsageDescription</key><string>Jarvis usa el micrófono solo cuando inicias el dictado. El audio permanece en memoria y se exige reconocimiento en este Mac.</string>
<key>NSSpeechRecognitionUsageDescription</key><string>Jarvis convierte tu voz a texto únicamente al iniciar el dictado y si el reconocimiento local está disponible.</string>
</dict></plist>
PLIST
/usr/bin/plutil -lint "$JARVIS_APP_PATH/Contents/Info.plist"
/usr/bin/codesign --force --sign - --timestamp=none "$JARVIS_APP_PATH"
/usr/bin/codesign --verify --strict "$JARVIS_APP_PATH"
printf '%s\n' 'Bundle de desarrollo verificado dentro del presupuesto. Su exportación requiere --output explícito.'
