# ADR-0015: Autoinicio persistente de la Menu Bar App

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** `SMAppService`, un helper embebido o un proyecto Xcode no son necesarios para el
   autoinicio local de este MVP firmado ad hoc.
2. **Eliminar:** no se añaden proceso guardián, instalador gráfico, dependencia ni ventana.
3. **Simplificar:** el bundle se instala en `~/Applications` y un LaunchAgent llama a `open` al login.
4. **Acelerar:** `build_and_run.sh --package` reutiliza exactamente el pipeline SwiftPM y de firma.
5. **Automatizar:** una orden empaqueta, valida, instala, registra y lanza la app.

## Decisión

`ai.aegis.menubar.autostart` usa `RunAtLoad` en la sesión Aqua y abre el bundle mediante
LaunchServices. No usa `KeepAlive`: cerrar Aegis es una decisión explícita y no debe provocar un
relanzamiento inmediato. El daemon separado sí mantiene `KeepAlive`.

La instalación copia primero a un bundle temporal, valida `codesign --strict` y solo entonces
reemplaza `~/Applications/AegisMenuBar.app`. El plist queda en `0600` y los logs en un directorio
`0700`. Desinstalar el servicio preserva el bundle y todos los datos.

## Encaje en el roadmap

- **Fase 3:** estabiliza la identidad y ruta del anfitrión que posee permisos TCC.
- **Fase 5:** completa la operación menu-bar-only al iniciar sesión, sin adelantar el HUD.

## Consecuencia

La app arranca automáticamente en la sesión del usuario y conserva “Salir” como acción real. La
firma ad hoc sigue siendo solo local; distribución pública y notarización requieren una identidad
Developer ID y permanecen fuera de este corte.
