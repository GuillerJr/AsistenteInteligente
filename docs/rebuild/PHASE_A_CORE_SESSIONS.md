# Núcleo de sesiones: contexto y continuación desde un punto

Este bloque añade contratos propios de contexto compacto y rewind conservador.
La referencia está fijada por [OC-21B](REA_PART21B_SUMMARY.json): tres fuentes
TypeScript y el manifiesto, con hashes y blobs Git. REA analizó esa partición
estática sin ejecutar OpenClaw; permanecen 866 incógnitas semánticas y las
dependencias no incluidas. El primer intento sobre el árbol de consulta se
rechazó por un cambio de entrada durante el inventario; no se relajó la
integridad ni se atribuyó cobertura a ese intento. Se creó una partición privada
inmutable desde `git show` del commit fijado, sin modificar el clon.

## Contrato de contexto

`sessions.context` consulta revisión y metadatos de un checkpoint. La revisión es
la secuencia del último mensaje de esa sesión. `sessions.compact` requiere sesión,
modo, revisión exacta y un `operation_id` de 32 caracteres hexadecimales. Presupuesto
de 4.096–16.000 bytes, cola de 1–40 mensajes y un máximo de 16 decisiones literales
de 1.024 bytes cada una. El inventario de mensajes tiene límite de lectura de 8 MiB.
El primer pedido elegible se conserva completo; se conservan las decisiones
elegidas expresamente por el dueño y las identidades, estados y hashes de los
recibos históricos. Si esos datos esenciales no caben, falla antes de cambiar nada.
Las decisiones de compactaciones posteriores se añaden sin duplicados y sin
eliminar las anteriores; si la unión supera 16, falla sin mutación. Un editor
vacío no descarta decisiones ya conservadas.
Los mensajes recientes adicionales entran completos o se omiten, con recuento.

La compactación es extractiva y determinista salvo la identidad del checkpoint;
no llama a otro proveedor ni presenta una síntesis de modelo como autoridad.
Mantiene los mensajes, runs, eventos y recibos originales. Las instrucciones del
perfil siguen en el canal propio de instrucciones, separado del historial.
El checkpoint se guarda como mensaje de sistema tipado del formato SQLite v2
existente: no añade tablas ni altera el formato ya cualificado de recuperación.
La app lo presenta como metadatos de conversación, sin mostrar el JSON interno.

Al compactar se elimina la asociación del hilo de proveedor. El turno siguiente
crea un hilo propio y `context_for_provider` entrega el checkpoint y la cola
posterior al transporte existente de Codex. El checkpoint se inyecta como datos
de usuario históricos, nunca como instrucciones de desarrollador o una nueva
solicitud de ejecutar herramientas. Se excluye la petición actual y los mensajes
de runs fallidos/interrumpidos; sus recibos permanecen en el ledger. El texto del
modelo y los recibos continúan separados. Un checkpoint no se descarta para
hacer caber una cola demasiado grande; la operación falla si su cápsula no cabe
en el presupuesto del transporte. Las omisiones no prometen conservar todo el
texto original en la ventana del modelo; el historial original sigue consultable.

## Continuación desde un punto

`sessions.rewind` requiere revisión, `operation_id`, secuencia de mensaje elegida
y título opcional. Crea una rama nueva con el mismo agente y modo temporal/durable.
La selección cambia el contexto conversacional: no deshace archivos ni efectos
externos. Conserva el origen y todos sus recibos, incluidos resultados inciertos
posteriores al corte. La nueva rama conserva ese ledger como dato histórico con
`effects_reverted=false` y `permits_replay=false`. No hereda hilos del proveedor,
runs, aprobaciones ni permisos. No ejecuta herramientas al crearla o inspeccionarla.
El corte acepta mensajes de usuario/asistente, no registros internos de metadatos.
La operación rechaza un origen ocupado y cambios de revisión, y publica la rama
y el registro de operación del padre en una sola transacción SQLite.

Los checkpoints, ramas y registros de una sesión temporal permanecen únicamente
en su SQLite RAM; no se consulta ni inicializa el store durable. Crear un hijo
con otro agente exige un padre existente en ese mismo modo y no copia su contexto.

## Ramificación completa y continuidad de correcciones

La auditoría posterior reprodujo un defecto concreto en la ruta antigua de
`sessions.fork`: copiaba sólo mensajes de usuario/asistente y perdía la decisión
literal «TOPACIO5192» que el origen ya había conservado en su checkpoint. La
misma omisión afectaba a referencias de recibos y correcciones de turno.
Ahora `StateStore.fork_session` utiliza el mismo motor transaccional de ramas,
conservando la cápsula de decisiones, recibos históricos y correcciones de
steering aceptadas o inciertas. Los registros de steering mantienen
`instructions_current=false`, `historical_ack_established=false` y
`permits_replay=false`: conservarlos no vuelve a enviar una corrección al proveedor.

La ruta productiva `sessions.fork` requiere `operation_id` y `expected_revision`;
el título opcional tiene un límite de 512 bytes. Devuelve sesión y lineage con
`kind=fork`, y su registro usa `operation_kind=fork`. Para una copia completa,
`until_sequence` coincide con la revisión del origen, incluidos sus metadatos
propios. El API no acepta un corte parcial: esa operación pertenece a
`sessions.rewind`. La firma interna histórica de StateStore conserva su corte
opcional y lo delega al contrato de rewind, sin copiar identidades de runs.

Una conversación vacía también admite una rama: revisión y punto cero, hija
sin mensajes ni contexto artificial, mismo modo y agente, sin hilo ni permisos.
La publicación de la hija y el recibo en el padre comparten una transacción.
La auditoría se añade al padre; sus mensajes, runs y recibos previos permanecen
intactos. Repetir una identidad/argumentos devuelve esa misma rama y un conflicto
se rechaza antes de publicar. La app usa el recorrido CAS con revisión del dueño;
no conserva una ruta alternativa de ramificación sin identidad de operación.

## Recibos de operación y pérdida de respuesta

Compactación, rewind y fork guardan su identidad, digest de argumentos y resultado en
la misma transacción que la mutación. Repetir la misma identidad y argumentos
devuelve la identidad del resultado registrado sin publicar una segunda rama o
checkpoint; argumentos distintos con la misma identidad se rechazan. La app
retiene la identidad antes del despacho y no vuelve a ejecutar por perder el ACK.
`sessions.operation` sólo consulta el registro local: `recorded=true`,
`historical_ack_established=false`, `permits_replay=false`. Una observación actual
del registro no acredita que la respuesta anterior haya sido recibida.
`sessions.operations` permite recuperar esos registros tras reiniciar sin conocer
de antemano la identidad: páginas de 1–100 entradas (20 por defecto), con offset,
`has_more` y `next_offset`. Selecciona sólo los registros de operación del ámbito
elegido, no carga el transcript completo ni dispara inferencia o mutaciones.

## Pruebas y alcance

- 18 casos propios de SQLite/contexto cubren temporalidad, origen intacto,
  identificación y decisiones literales, recibos inciertos, cola posterior,
  revisión obsoleta, presupuestos en bytes, rollback transaccional, reinicio,
  deduplicación durable, conflicto de identidad, rechazo de roles elevados y
  consulta de recibos/metadatos sin cargar un transcript que supera el límite,
  paginación acotada e independencia de ámbitos.
- Cinco casos RPC usan sockets reales, SQLite y un peer de inferencia propio:
  compactación durable/temporal consumida por el siguiente turno con instrucciones
  de perfil intactas; rewind sin binding/duplicación; rechazo de revisiones y
  parámetros inválidos y catálogo paginado de operaciones sin inferencia. Son
  integración con proveedor sintético, no inferencia
  autenticada de una cuenta real.
- Un caso de protocolo produce el checkpoint desde StateStore y lo entrega al
  cliente Codex real frente a un servidor stdio propio. Comprueba el contenido
  exacto de `thread/inject_items`, su rol de datos, instrucciones de perfil
  separadas y su orden anterior a `turn/start`.
- La primera ejecución RPC con permiso de socket reprodujo tres fallos porque
  el servicio todavía usaba la selección antigua de historial y no consumía el
  checkpoint. Tras integrar `context_for_provider`, pasaron los 33 casos focales
  iniciales en 1,55 s. La selección ampliada de contexto/estado de
  sesiones/Codex/RPC pasó 35 casos en 4,15 s. El cierre posterior con catálogo
  de operaciones pasó **38 casos en 1,51 s**, en
  `/private/tmp/jarvis-core-session-python-final.log`. Un intento intermedio
  tuvo 37 aprobados y un timeout al inicializar el peer sintético con techo de
  un segundo, antes de llegar a la aserción del límite de historial. Sin cambiar
  timeout, código productivo ni aserciones, el caso aislado y el catálogo RPC
  pasaron (dos casos en 0,24 s) y después pasó la selección completa. No se
  atribuye una causa de sistema precisa a ese timeout. Los 99 casos de estado y
  sesiones pasaron en otra selección solapada; no se suman como pruebas distintas.
- El sandbox rechazó inicialmente `bind(AF_UNIX)`; se repitieron esos casos con
  ejecución autorizada fuera del sandbox, siempre sobre perfiles sintéticos nuevos.

La integración nativa adicional `SessionCoreIntegrationTests.swift` ejecuta
Swift → RPC firmado → Python/SQLite reales con un peer de modelo propio. Crea
contexto compacto y rama, comprueba las fuentes y los nueve runs originales,
continúa desde la rama y cierra el host. Lo arranca dos veces más: conserva las
identidades de checkpoints/ramas y las páginas de recibos, desaparece la sesión
temporal y no hay inferencia ni replay por consultar o reiniciar. Para perder un
ACK de verdad, el fixture publica primero el checkpoint SQL y retiene la
respuesta hasta que el socket Swift agota su timeout; el Store conserva la
identidad y consulta el recibo sin reenviar la mutación. El registro sobrevive
al segundo reinicio. Otro caso rechaza un recibo de compactación de otra sesión.

La revisión independiente detectó la ausencia de vínculo de sesión/modo en la
lectura nativa de un recibo compacto y el desacuerdo `next_offset=null` al acabar
una página. Se corrigieron los validadores nativos y se probaron con el servidor
real, conservando el contrato Python. La primera compilación del nuevo fixture
requirió mover awaits fuera de autoclosures XCTest; otra planificación coincidió
con la incorporación aún incompleta de la vista de delegación. Ningún intento
fallido se presenta como prueba ejecutada. Tras integrar los archivos,
**siete pruebas Swift de contexto pasaron sin fallos en 1,445 s** (dos de
integración y cinco unitarias). Log:
`/private/tmp/jarvis-session-core-swift-final.log`. Esta evidencia es integración
nativa con proveedor sintético, no observación de la GUI ni inferencia autenticada.

Después de corregir la ruta antigua de fork pasaron **28 casos Python focales
en 1,95 s**, incluidos cinco nuevos casos del motor de fork, los dos recorridos
RPC temporal/durable y los dos casos previos de escritura/ramificación con
aprobación sintética y efecto ejecutado una sola vez. También incluyen las nueve
regresiones de continuidad de steering. Log:
`/private/tmp/jarvis-session-fork-python.log`. Estos conjuntos se solapan con las
selecciones anteriores y no se suman como casos independientes.
La selección Swift final con la nueva ramificación pasó **nueve casos sin fallos
en 2,174 s**, incluidos gestión previa y contexto. La integración comprueba una
rama completa con dos decisiones y nueve recibos heredados como datos, una rama
vacía con revisión cero y la consulta durable de ambas tras reiniciar. Log:
`/private/tmp/jarvis-session-core-fork-swift-final.log`. Una planificación previa
no ejecutó esos tests porque el nuevo fixture de colas tenía un error de etiqueta
Swift; se repitió tras la corrección de ese archivo.

La interfaz y sus recorridos nativos se cualifican en el registro integrado del
bloque de núcleo. Este documento no declara paridad de proyectos/Git/worktrees,
rewind de repositorios, colaboración remota ni del harness completo de referencia.
