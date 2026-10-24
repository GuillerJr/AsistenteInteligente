# Primer recorrido de la base, 24-10-2026

Este documento registra una implementación de desarrollo, **no el cierre de la
fase A**. El alcance completo sigue en `OPENCLAW_BASELINE_PARITY.json`. No se han
integrado mejoras adicionales ni herramientas del producto anterior.

Este registro describe el primer recorrido (commits `4deaedd` y `6289f37`). El
avance posterior de herramientas, memoria y MCP y su verificación se documentan
en [el bloque siguiente](PHASE_A_TOOLS_MEMORY_MCP.md); los límites de esta primera
integración no deben confundirse con el estado posterior.

## Implementación conectada

- `state.py`: SQLite privado, sesiones, mensajes, runs, eventos, paginación y
  vinculación con hilos propios del proveedor. Los temporales usan sólo RAM.
- `runtime.py`: serialización, recibos, cancelación y espera de limpieza.
- `protocol.py`: JSON-RPC por socket Unix, token privado, límites y plazos.
- `codex.py`: proceso oficial `codex app-server`, handshake, login/logout,
  lectura saneada de estado, turnos, streaming, interrupción y cierre.
- `service.py`: conecta esos componentes mediante API local, con identidades de
  solicitud idempotentes y ninguna repetición automática de un turno incierto.
- `native/JarvisDesktop`: cliente SwiftUI propio con ventana, Dock, barra de menús,
  atajo, sesiones, chat temporal, conexión, envío, actividad y cancelación.

Codex utiliza HOME, CODEX_HOME y espacio de trabajo nuevos y privados. Jarvis no
lee `auth.json`, no importa credenciales ni enumera hilos de otro producto. La
cuenta y sus secretos los gestiona el cliente oficial; el dueño completa el acceso.
El protocolo se contrastó con la [documentación oficial](https://learn.chatgpt.com/docs/app-server)
y esquemas generados en `/private/tmp` por Codex CLI **0.163.0**, sin copiar su
implementación. No se fija ni sustituye un modelo elegido por el dueño.

El adaptador inicial es de chat: herramientas incorporadas desactivadas,
solicitudes entrantes de acciones rechazadas y sin conectores heredados. Conectar
herramientas y decisiones reales a una autoridad común sigue siendo trabajo de A.

## Límites que se comprueban

El mensaje y la respuesta del proveedor se acotan a 32000 bytes. El transporte
admite 1 MiB; las páginas de historial se recortan por tamaño y secuencia. El
almacén limita filas, texto y JSON, y reserva el evento de cierre de cada run.

Crear un run con su mensaje y cerrar su recibo con la respuesta son transacciones
atómicas. Tras una caída, un run activo pasa a `interrupted/unknown`; se invalida
su vínculo remoto y no se reejecuta. Un ACK de `turn/interrupt` no se interpreta
como cierre: se espera el evento terminal o se detiene el proceso propio y se
cuarentenan sus hilos. Los efectos reportados no se presentan como verificados.

## Qué acredita la evidencia

Las pruebas Python usan sockets, SQLite y procesos locales reales con contenido
sintético. Los proveedores de esas pruebas simulan respuestas: no acreditan acceso
a un modelo ni una cuenta. Incluyen caída abrupta de proceso, corrupción, enlaces,
permisos, rollback, revocación del token, mensajes inválidos, errores, cancelación,
cierre concurrente y recuperación. Swift prueba su cliente contra Python real.

El dueño completó el acceso oficial en el perfil de prueba nuevo. Desde la app
se confirmó `Codex con ChatGPT conectado` y respuesta real `BASE JARVIS OK`. Se
guardó un marcador sintético, se cerró y reabrió app/servicio y Codex recuperó la
palabra `NARANJA` sin volver a enviársela. El chat temporal desapareció al reiniciar.
También se canceló una generación larga y la app mostró `Cancelado y asentado`.
Estos recorridos no utilizaron archivos personales ni herramientas externas.

La suite completa con `jarvis_core` aprobó **303 pruebas Python**. El cliente
Swift aprobó **13 pruebas**, con los plazos de arranque corregidos. El cambio de
identidad y el recorrido de chat no cierran la paridad completa.

## Distribución y límites abiertos

`script/build_desktop.sh` genera un bundle de desarrollo con firma ad hoc y bundle
ID `com.jarvis.desktop.baseline`. Recibe rutas explícitas a Python, fuente, estado y
Codex. No incluye intérprete ni el cliente oficial, y no sustituye `Jarvis.app`
instalado. Una firma ad hoc no equivale a Developer ID ni notarización.

No hay identidad de cliente firmada en el IPC, sandbox de herramientas cualificado,
MCP conectado a la app, memoria semántica, canales, nodos, audio ni herramientas
completas de la base. Tampoco hay migración de datos del producto anterior. El
inventario MCP puede incluir un cliente con pruebas sin que eso lo haga una
función disponible en la interfaz. Esas entradas permanecen abiertas en la matriz.

## Identidad del proyecto

Por orden del dueño, carpetas, archivos, código, scripts y metadatos usan Jarvis.
Paquete `jarvis_core`, distribución `jarvis-core`, SQLite `JRVS`/`jarvis.sqlite3`,
protocolo `jarvis-local-rpc` y bundle `com.jarvis.desktop.baseline`. El entorno
editable se reinstaló desde esta fuente y los metadatos generados anteriores
quedaron fuera del árbol activo. La auditoría de nombres del árbol de trabajo
no encontró coincidencias del identificador retirado. El historial Git conserva
los originales; los inventarios de consulta normalizan nombres con esa trazabilidad.

La comprobación visual del primer arranque tras renombrar mostró un error, aunque
un cliente Swift pudo consultar después ese mismo servicio propio sano. El
bootstrap ocultaba el último error tras cinco segundos y el RPC usaba sólo tres
segundos frente a operaciones del proveedor de hasta veinte. Se corrigen esos
plazos y la comunicación del error, y se ensayan un arranque deliberadamente
lento de seis segundos y una respuesta de cuatro. Esa reproducción acredita el
defecto de presupuesto; no mide ni atribuye el retraso observado a iCloud. No se
inician procesos duplicados ni se repiten mutaciones durante la espera.

Verificación final del bundle renombrado: inició con ventana `jarvis.main`, mostró
la conexión a ChatGPT y devolvió `JARVIS LISTO` desde una conversación sintética.
El bundle de desarrollo validado queda en
`/private/tmp/jarvis-desktop-bootstrap-20261024/Jarvis.app`.

Con la identidad definitiva también se cerró y reabrió Jarvis y Codex recuperó
`JARVIS LISTO` desde su hilo propio, sin volver a suministrarle ese texto. Las
pruebas de cancelación en vivo y desaparición del temporal descritas arriba se
hicieron antes del renombrado; sus contratos vuelven a probarse automáticamente.
