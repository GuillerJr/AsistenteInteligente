# ADR-0043: Descubrimiento dinámico del SDK macOS

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** fijar `MacOSX26.5.sdk` convierte una actualización normal de Xcode en una rotura
   artificial del build.
2. **Eliminar:** se elimina la versión duplicada de tres scripts; no se añade gestor de toolchains.
3. **Simplificar:** `xcrun` propone el SDK y su interfaz declara si `@State` exige un macro host.
4. **Acelerar:** build, pruebas y entrenador usan la misma regla sin configuración inicial.
5. **Automatizar:** una actualización del SDK se adopta en la siguiente ejecución.

## Decisión

`resolve_macos_sdk.sh` conserva `AEGIS_MACOS_SDK` como override. Si está vacío, consulta los binarios
absolutos `/usr/bin/xcrun` y `swiftc`. La interfaz arm64e de `SwiftUICore` declara `StateMacro` cuando
`@State` depende de `SwiftUIMacros`; el resolver exige entonces el plugin correspondiente en el
directorio host del mismo toolchain. La inspección es de solo lectura y no compila un proyecto de
prueba.

Si el SDK activo falla, el resolver prueba los SDKs versionados reales del mismo directorio y elige
el compatible con mayor versión. Build, pruebas y entrenamiento llaman al único resolver;
`release_macos.sh`, la instalación de Menu Bar y la activación del wake word heredan esa decisión.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** mantiene reproducible el entrenador local.
- **Fase 5 — Interfaz y distribución:** evita acoplar el bundle a una versión puntual del SDK.

## Consecuencia

Jarvis continúa compilando para Apple Silicon aunque el enlace `MacOSX.sdk` adelante a un SDK que el
host Swift aún no puede expandir. En esta máquina, SDK 27.0 falla por ausencia de `SwiftUIMacros` y
el resolver selecciona 26.5. Un override inválido falla y no degrada a otro SDK.
