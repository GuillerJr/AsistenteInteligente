# Fase A: herramientas, memoria local y MCP

Avance del 24-10-2026. Implementación propia de familias de la base OpenClaw,
sin importar su código, ejecutar su clon ni adelantar herramientas adicionales
del Jarvis anterior. Este bloque no cierra la matriz de 80 capacidades.

## Investigación y decisiones

Los lotes REA OC-05 y OC-06 fijan ocho archivos TypeScript adicionales. Hay seis
lotes y 24 rutas TypeScript examinadas; la cobertura global sigue siendo parcial.
Cada manifiesto conserva rutas y hashes, y cada resumen los Evidence IDs. El
bundle completo de estos dos lotes se exportó a
`/private/tmp/jarvis-openclaw-rea-phase-a-parts05-06-20261024.json` (6.586.566 bytes).

- OC-05: políticas por capas y entrega de resultados de herramientas dinámicas.
  Observación estática: los estados de inicio del efecto y entrega de respuesta
  son distintos. Decisión propia: un fallo de transporte tras despachar conserva
  un resultado incierto; no provoca repetir la operación automáticamente.
- OC-06: búsqueda, scoring y escritura de chunks. Observaciones estáticas: el
  escritor conserva procedencia, el scorer comprueba coordenadas finitas y el
  esquema seleccionado reexporta contratos de otros módulos. No se revisaron
  aquí esos contratos externos ni se ejecutó OpenClaw. Decisión propia: memoria
  local con límites, revisión y hashes verificables; búsqueda léxica identificada
  explícitamente, sin afirmar embeddings o recuperación semántica.

188 y 887 incógnitas semánticas permanecen respectivamente en los lotes. Ningún
recuento AST ni archivo inventariado acredita por sí mismo un comportamiento.

## Recorrido conectado

Codex oficial recibe únicamente el catálogo de Jarvis mediante `dynamicTools`.
Sus herramientas incorporadas continúan desactivadas. Las llamadas entrantes se
traducen a propuestas de `AgentRuntime`: el adaptador del proveedor no ejecuta.
Una sola autoridad liga la decisión a sesión, ejecución, operación, argumentos,
digest, caducidad y versión de revocación. El permiso se comprueba de nuevo justo
antes del efecto. Una tarjeta caducada, revocada o perteneciente a otro run no
puede despachar. Si desaparece el turno del proveedor mientras espera, se anula.

El lector del protocolo sigue atendiendo eventos y cancelación mientras una
herramienta espera al dueño. El resultado técnico se entrega al modelo, pero el
recibo de la herramienta se conserva y muestra por separado. La vista del recibo
acota su output a 512 bytes e informa tamaño, hash y truncado. Una respuesta del
modelo nunca convierte un resultado externo en verificación independiente.

Al cambiar el catálogo o la carpeta se abre un hilo propio nuevo: Codex 0.163.0
no admite reemplazar `dynamicTools` en `thread/resume`. Sólo se repone contexto
completado de esa sesión de Jarvis, acotado a 100 mensajes y 16.000 bytes. No se
enumeran ni importan hilos de otras apps o cuentas.

## Archivos y memoria

Herramientas activas: listar, leer, crear/escribir, editar texto y crear carpeta;
recordar, guardar y olvidar memoria. Toda mutación pide aprobación por operación.
El espacio predeterminado está dentro del perfil nuevo; una carpeta externa se
elige expresamente con el selector nativo. La raíz conserva identidad de inode;
no hay retarget silencioso. Traversal, enlaces, archivos privados y escrituras
sobre versiones que cambiaron se rechazan. Una edición exige hash previo y una
coincidencia única, y relee el resultado para verificar bytes.

La memoria SQLite se separa por agente y carpeta. Los chats durables comparten
memoria de esa carpeta; cada temporal tiene RAM independiente y no abre la base
durable para consultar sus documentos. No se indexan datos del dueño de forma
automática. Una fuente es una etiqueta aportada, no una web consultada o validada.
Las citas ligan revisión, hash y rango de bytes; una actualización o borrado las
invalida. La búsqueda expone su modo local FTS5 o exploración léxica y la generación
del índice para evitar mezclar páginas de versiones diferentes.

## MCP en la app

La configuración privada guarda hasta ocho servidores explícitos. «Añadir» no
lanza procesos. «Conectar» requiere al dueño revisar programa, argumentos y
versión de protocolo en una confirmación nativa. El backend coteja ese snapshot
antes de arrancar; no conecta al iniciar ni usa un fallback de versión.
Las versiones admitidas son 2025-11-25 y 2026-07-28 mediante stdio.

El registro transforma cada tool a un nombre ligado a servidor, comando y
esquema. Cada llamada necesita la autoridad común, incluso si el servidor afirma
ser de sólo lectura. El permiso se consume bajo el lock de escritura, antes del
primer byte de `tools/call`. Cambios de catálogo invalidan los nombres previos.
No se cambia de conexión mientras hay un run activo, ni se inicia un run durante
el mantenimiento. Eliminar libera proceso y plaza del registro. Una pérdida de
ACK tras el envío conserva incertidumbre y no se reconecta/repite por su cuenta.

El proceso usa HOME y entorno acotados, pero ejecuta como el usuario: esto no es
un sandbox del sistema operativo. Los resultados MCP se etiquetan como reportes
externos sin verificación independiente. OAuth, HTTP/SSE y el catálogo completo
de apps y recursos de referencia permanecen pendientes.

## Verificación y límites

Las suites ejercitan archivos, SQLite, sockets Unix y subprocess stdio reales
con contenido y decisiones sintéticos. Cubren rechazo, revocación, cierre del
proveedor, concurrencia, contexto, citas obsoletas, errores tras commit, límites,
errores de protocolo, cambio de catálogo y eliminación/recreación de servidores.
Las pruebas de contrato no sustituyen la aprobación real del dueño ni certifican
un servidor externo. Los resultados finales y el recorrido nativo se registran
en `PHASE_A_QUALIFICATION.json`.

El conjunto final aprobó 656 pruebas Python y 31 Swift. Desde la app de prueba con
Codex oficial se leyó un marcador, el dueño aprobó crear un archivo y se cotejaron
sus 21 bytes y hash con los recibos de escritura/relectura. También aprobó una nota
local: la búsqueda y cita nativas coincidieron, y otro chat la recuperó tras
reiniciar. El dueño conectó un servidor MCP propio de fixture y aprobó una llamada;
el log independiente confirma una única invocación y la app conserva su resultado.

Se observó un límite de narración del modelo: algunos resúmenes dijeron que faltaba
contenido aunque el recibo MCP contenía el marcador. El adaptador conserva ahora
el recibo JSON y entrega además la salida como texto identificada según su
verificación. Una prueba con inferencia oficial y reporte sintético reprodujo el
marcador, pero no acredita fiabilidad determinista del resumen. La app muestra el
recibo separado para que esa narración no esconda ni certifique el efecto.

La app sigue siendo un bundle de desarrollo con Python, fuentes y cliente Codex
externos. La instalación anterior y los datos del dueño no se reemplazan. Siguen
abiertas, entre otras, distribución autónoma, IPC firmado, sandbox, herramientas
completas de referencia, sesiones avanzadas, agentes, skills, programador, canales,
nodos, navegador, escritorio y audio. Todas pertenecen a A cuando son base OpenClaw.
Las mejoras adicionales de B esperan el cierre de esa matriz.
