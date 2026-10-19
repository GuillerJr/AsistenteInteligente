# Punto 10 — base de admisión MCP

Fecha: 2026-09-14. Prioridad elegida: MCP externo de solo lectura.
Estado: controles stdio implementados y probados; ningún servicio externo conectado o promovido.

## Resultado

Configuración opt-in, nombres exactos de herramientas con huellas de entrada aprobadas, aplicaciones
explícitas, confirmación de cada llamada y cuarentena por peer. La configuración inválida desactiva
MCP sin impedir arrancar el resto de Jarvis. Se preservan los fallos cerrados de auditoría.

Los cambios de catálogo revocan visibilidad y ejecución mediante handlers conservados. Se prueban
esquemas cambiados, herramientas ausentes o adicionales, duplicados, paginación circular, claims de
escritura, cancelación durante arranque/llamada, timeout, exceso de stdout y cierre concurrente.
Los diagnósticos offline/runtime no muestran argumentos, rutas, entorno o respuestas del proveedor.

Comandos: `mcp-check`, `mcp-schema ARCHIVO` y `mcp-status`. La plantilla
`examples/mcp/read-only.template.json` está inactiva y sin aprobaciones.

## Evidencia

- Suite Python completa: **1.737 aprobadas, 17 omitidas**, en 34,09 segundos.
- Ruff sobre `src` y `tests`: correcto.
- `git diff --check`: correcto.
- CLI `--help`: correcto. Las rutas nuevas tienen pruebas de despacho, errores y resultados.

La primera suite bajo sandbox falló en 38 pruebas que necesitan sockets IPC locales. La repetición
autorizada fuera del sandbox pasó sin rebajar aserciones. Los peers MCP usados son procesos fixture
locales; no leen voz, no llaman servicios reales ni controlan dispositivos. No se recompiló la app
nativa ni se sustituyó el paquete de distribución anterior: estos cambios están en el repositorio.

## Límites pendientes

- Context7 es una recomendación, no una elección confirmada ni una instalación. Deben revisarse
  servidor/versión, transporte compatible, datos enviados y sus dos herramientas de documentación.
- La huella fija inputSchema, no código, identidad ni ausencia de efectos. El proceso stdio conserva
  los permisos de la cuenta; no es un sandbox. El proveedor debe limitar sus permisos a lectura.
- Los Capability Packs HTTPS tienen otro runtime y no están certificados por esta fase.
- La visibilidad por aplicación no incorpora automáticamente MCP al terminal de ingeniería.
- Dispositivos y funciones experimentales no fueron habilitados; pruebas físicas/vocales aplazadas.
- Configuraciones antiguas necesitan revisión manual y el daemon instalado deberá actualizarse
  antes de usar `mcp.status`. No hubo migración de datos reales, instalación ni reinicio.

Contrato y fuentes: [ADR-0226](../architecture/ADR-0226-reviewed-mcp-admission.md).
