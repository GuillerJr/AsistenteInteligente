# ADR-0214 — Sesión de ingeniería separada del daemon y del terminal

- Estado: aceptado para el pulido de la capacidad existente `engineering_cli`.
- Responsable: Guillermo (`gzambrano27`).
- Base: `a6a7555`. No incorpora herramientas, modelos ni autoridad nueva.

`engineering.py` conserva contratos, confinamiento, inventario e instrucciones del daemon.
`engineering_cli.py` posee una conversación y una sola tarea: preflight, seguimiento, cancelación
y recuperación. `engineering_terminal.py` presenta datos sin interpretar controles externos.
`engineering_input.py` carga `prompt_toolkit` solo para edición interactiva asíncrona; no se
introducen hilos bloqueados en `readline`, archivos de historial ni ciclos de refresco en reposo.

Se prefiere una biblioteca mantenida de edición frente a recrear un emulador de terminal. La
dependencia y `wcwidth` quedan fijadas en `uv.lock`. El historial está acotado en bytes y registros;
es distinto de la memoria conversacional cifrada que ya conserva el daemon.

Ctrl-C interrumpe la operación local y solicita `jobs.cancel` para el ID conocido. La cancelación
se confirma con un snapshot autenticado y terminal, con un máximo de cinco segundos de espera.
Una desconexión no reenvía la instrucción. `/resume` usa `jobs.status` y vuelve a `jobs.wait`.
Cada snapshot mantiene job ID, request ID, conversación y versión monotónica. Ningún comando del
CLI emite `jobs.approve`. La autorización interactiva se sigue con una pausa de 500 ms: el contrato
del daemon devuelve inmediatamente las aprobaciones pendientes y no debe provocar un bucle activo.

En modo puntual stdout contiene únicamente la respuesta; diagnósticos van a stderr. stdin se
interpreta como una única solicitud literal acotada. El texto procedente de modelos o archivos no
puede emitir ANSI, OSC52 ni controles bidi. Los errores presentan códigos y acciones fijas, no
excepciones privadas del proveedor. `offline` y `local_only` se explican como políticas diferentes.

Pruebas: unidades de streaming/identidad/recuperación, límites del editor y secuencias maliciosas;
proceso real en PTY conectado a UDS autenticado para pegado, SIGINT, historial y EOF. Se mantienen
los controles completos de commit/push. La certificación de voz no forma parte de este bloque.
