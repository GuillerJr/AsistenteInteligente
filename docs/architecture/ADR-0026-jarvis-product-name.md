# ADR-0026: Jarvis como nombre de producto

- Estado: aceptado
- Fecha: 2026-08-20

## Filtro de ingeniería

1. **Cuestionar:** cambiar toda referencia `aegis` no aporta una función y rompería estado local.
2. **Eliminar:** no se duplica configuración ni se crea una capa de migración permanente.
3. **Simplificar:** solo cambian el nombre visible, producto Swift, ejecutable, bundle y artefacto.
4. **Acelerar:** el target Swift y los módulos internos conservan sus nombres actuales.
5. **Automatizar:** la instalación valida el bundle anterior y lo reemplaza después de arrancar
   Jarvis correctamente.

## Decisión

El producto visible se llama `Jarvis`; su bundle y proceso son `Jarvis.app` y `Jarvis`. Se conserva
el identificador `ai.aegis.menubar` para que macOS mantenga las decisiones TCC existentes.

También se conservan `aegis_core`, el comando `aegis`, las variables `AEGIS_*`, los servicios
`ai.aegis.*`, los labels de LaunchAgent y `~/Library/Application Support/Aegis`. Son identificadores
persistentes, no marca visible. Cambiarlos exigiría migrar credenciales, memoria, permisos y estado
sin beneficio funcional.

## Consecuencias

El usuario ve Jarvis en la Menu Bar, ventanas, voz, procesos y artefactos de distribución. Una
instalación existente se migra a `~/Applications/Jarvis.app`; el bundle anterior solo se elimina
después de validar firma y arranque del reemplazo. Los datos y secretos no se copian ni se exponen.
