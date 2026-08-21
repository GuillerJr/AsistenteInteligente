# ADR-0044: Swift del toolchain activo

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** resolver el SDK con `xcrun` y luego ejecutar `swift` desde `PATH` permite mezclar
   dos toolchains en la misma compilación.
2. **Eliminar:** no se añade selector, shim, gestor de versiones ni configuración persistente.
3. **Simplificar:** los tres scripts consultan `/usr/bin/xcrun --find swift`.
4. **Acelerar:** no hay búsqueda adicional de PATH ni reintento con otro compilador.
5. **Automatizar:** cambiar `xcode-select` mueve conjuntamente Swift y el SDK evaluado.

## Decisión

`build_and_run.sh`, `test_native.sh` y `train_wake_word.sh` guardan la ruta absoluta que devuelve
`xcrun`, exigen que sea ejecutable y la usan para `build`, `test` y `run`. Las variables de caché y
`SDKROOT` existentes se conservan sin alteración.

El resolver de SDK continúa usando `swiftc` del mismo origen. Por tanto, la selección de macros
SwiftUI, la compilación de Jarvis y el entrenador Create ML comparten la autoridad de
`xcode-select`. `PATH` permanece disponible únicamente para utilidades no relacionadas.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** alinea el compilador del entrenador y su SDK.
- **Fase 5 — Interfaz y distribución:** hace reproducibles Debug, pruebas y Release.

## Consecuencia

Una instalación de Swift en Homebrew o una modificación de `PATH` no cambia el compilador de
Jarvis. Un toolchain activo incompleto falla antes de comenzar SwiftPM.
