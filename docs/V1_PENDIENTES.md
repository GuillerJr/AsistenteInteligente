# Pendientes de Jarvis

Decisión del dueño, 24-10-2026: **primero construir la variante completa funcional
de OpenClaw con el nombre Jarvis, mediante investigación con REA y código propio**.
Las mejoras propias se harán después. Se limpió este repositorio; el código del
producto anterior sigue en Git y sus inventarios están congelados.

Alcance inicial elegido: macOS Apple Silicon y Telegram como primer canal externo.
El dueño aplazó expresamente la app de iPhone; ver [decisiones](rebuild/PHASE_A_TARGETS.md).

## Fase A — base completa primero

- [x] Limpiar el árbol de trabajo y conservar puntos de recuperación.
- [x] Conservar inventarios de capacidades del Jarvis anterior para la fase posterior.
- [x] Fijar la referencia de OpenClaw y empezar su análisis REA por bloques.
- [ ] Completar la investigación y los recorridos de la
  [matriz de paridad](rebuild/OPENCLAW_BASELINE_PARITY.md); el censo no es revisión.
- [ ] Cualificar el producto: el recorrido de desarrollo ya acredita app SwiftUI,
  servicio, IPC, sesiones SQLite, inferencia autenticada con Codex oficial,
  cancelación, reinicio y temporales. Falta cualificación de distribución completa.
- [ ] Implementar y verificar las herramientas de la propia base OpenClaw, sesiones
  avanzadas, agentes, memoria base, MCP, skills, extensiones, programador, canales,
  nodos y superficies funcionales incluidas en la matriz. No moverlas a la fase B
  por coincidir con capacidades del Jarvis anterior.
  Avance conectado: autoridad de herramientas, archivos, memoria léxica local y
  gestión MCP stdio desde la app; ver [alcance y límites](rebuild/PHASE_A_TOOLS_MEMORY_MCP.md).
  La [continuación de gestión](rebuild/PHASE_A_MANAGEMENT.md) añade procesos aislados,
  perfiles de agente, biblioteca local de skills, programador y ramas de conversación.
- [ ] Completar aislamiento de ejecución y autorizaciones reales del dueño, sin
  confundir token local con identidad firmada, ni un ACK con cierre de una acción.
- [ ] Distribución nativa autocontenida, actualización, recuperación e instalación
  cualificadas; no reemplazar la app anterior con un bundle de desarrollo.
- [ ] Acreditar la base completa mediante pruebas por capacidad y recorridos reales.
  La fase A sigue abierta hasta cumplir los criterios de la matriz.

## Continuación de canales, voz y web — cualificación pendiente

El [nuevo registro](rebuild/PHASE_A_CHANNELS_VOICE_WEB.md) documenta Telegram
privado como primer canal externo, vinculación con dos revisiones del dueño,
voz local y lectura HTTPS autorizada por URL. Hay controles locales de un
snapshot anterior; los ajustes posteriores de supervisión nativa y cierre web
requieren repetir la cualificación final. El dueño ya completó las dos revisiones
de vinculación, confirmó una ruta y activó el canal; el control Iniciar fue ignorado sin run. Una suma de prueba recibió 4 con
salida confirmada y sin herramientas. Se corrige el refresh nativo del chat
seleccionado; faltan el resto de recorridos del canal, voz y web reales. Después el
dueño desconectó; el servicio anterior cerró antes de cargar el reemplazo. Estas
observaciones corresponden al ejecutable anterior al fix, sin herramientas.

- [ ] Cualificar Telegram con chat privado elegido por el dueño: entrada/respuesta
  únicas, agente/sesión/destino exactos, recibo durable y denegación/revocación.
- [x] Observar ambas revisiones de vinculación, creación de ruta y una suma real
  de prueba con salida confirmada, sin herramientas; alcance limitado registrado.
- [ ] Completar comprobaciones reales de expiración/cancelación y del refresh nativo,
  sin ruta implícita, mensaje automático ni comandos `/start` enviados al modelo.
- [ ] Cualificar dictado y lectura locales en el Mac objetivo, con TCC del dueño,
  borrador conservado, sin autoSend ni audio a nube, NVIDIA o proveedor no elegido.
- [ ] Cualificar `web_fetch` por la app: aprobación/revocación, límites y cita externa,
  sin buscador, JavaScript, cuentas ni descarga de archivos.
- [ ] Registrar controles finales de cierre Codex/MCP/Telegram/scheduler/RPC,
  reteniendo Stores y propietarios ante incertidumbre y sin reconexión/replay.
- [ ] Completar la investigación semántica pendiente: REA 10–12 amplían selección y
  hashes, pero doce lotes y 48 rutas TS/JS no cierran la matriz ni toda la referencia.
- [ ] Repetir Python/Swift/paquete/CI tras los últimos ajustes y registrar identidades
  finales. No sustituir la instalación anterior por un bundle de desarrollo.

macOS Apple Silicon sigue como destino inicial e iPhone permanece aplazado por
petición expresa. Estas implementaciones parciales no equivalen a todos los
canales, navegador completo o voz continua. La fase A continúa abierta.

## Fase B — después de acreditar A

- [ ] Mejoras propias y herramientas adicionales del
  [inventario backend](rebuild/JARVIS_BACKEND_INVENTORY.md) y
  [nativo](rebuild/JARVIS_NATIVE_INVENTORY.md).
- [ ] Migración reversible y ensayada con datos sintéticos del producto anterior.
- [ ] Repetir las pruebas históricas que correspondan, incluida aprobación,
  revocación y verificación real de resultados. No se declaran resueltas por rehacer.

La conexión elegida sigue siendo Codex con ChatGPT; NVIDIA queda excluido y no hay
respaldo automático entre proveedores. El dueño inicia sesión, escribe contraseñas,
concede TCC y aprueba acciones reales. Los pendientes anteriores permanecen en el
historial del commit `c97ed89`; no se han borrado de los inventarios.

Checkpoint local posterior: 1234 pruebas Python y 129 nativas aprobadas. Se verificaron
una escritura desde Telegram con aprobación y hash, una lectura HTTPS marcada como
externa y un dictado local de español añadido al borrador sin enviarse. Son recorridos
acotados en el perfil sintético, no cierre de las familias ni de la fase A.
