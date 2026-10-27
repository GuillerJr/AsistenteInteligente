# Coordinación, colas y presupuestos del núcleo

Este contrato propio conecta el núcleo, el servicio y la app. La lectura de
referencia está fijada en [OC-21C](REA_PART21C_SUMMARY.json): cuatro fuentes TS y
su manifiesto, obtenidos de objetos Git de la revisión conocida. REA conserva
el análisis estático y sus incógnitas. No se ejecutó OpenClaw ni se importó su
implementación. Una lectura previa del árbol vivo falló por cambio de identidad
de `pnpm-lock.yaml`; se conserva ese fallo y se usó otra partición inmutable.

## Un objetivo y sus hijos

`delegations.start` es un control explícito del cliente admitido. Elige la sesión
padre, un objetivo y entre una y cuatro tareas con agente/revisión exactos.
El objetivo ocupa una ejecución propia; sus hijos son sesiones nuevas con
`parent_session_id`, mismo modo durable/temporal y el perfil elegido. No copian
historial, binding de proveedor ni permisos de la conversación padre.

La herramienta de modelo `agents_delegate` necesita una aprobación de la
autoridad común ligada al run, sesión, operación y argumentos. Sus hijos entran
por el mismo `start_run`, proveedor elegido, barrera de perfil, Runtime y recibo
SQLite. Cada efecto del hijo necesita su propia aprobación; aprobar la
delegación no autoriza una escritura. Durante una tarea hija el catálogo efectivo
excluye delegación recursiva. Este límite de un nivel es una decisión explícita
del producto para mantener presupuesto y propiedad finitos.

`agents_available` permite consultar IDs y revisiones actuales antes de proponer
una delegación. Devuelve únicamente etiquetas del registro local, sin exportar
instrucciones privadas, historial ni permisos. El agente elegido y su revisión
se comprueban otra vez al admitir el objetivo y antes de iniciar cada hijo.

El padre controla sólo los hijos de su objetivo. `delegations.get`, `list` y
`wait` son observaciones; un timeout de espera no cancela, consume resultados ni
inicia una continuación. `delegations.cancel` solicita cancelar el padre y sus
hijos y devuelve un recibo de aceptación separado del cierre real.

## Presupuesto y cierre

Hay como máximo dos objetivos activos, cuatro hijos por objetivo y ocho lanes
de Runtime. Cada hijo reserva entre 1 y 32 llamadas `provider.next_step`; la suma
no puede superar el presupuesto total explícito, entre 1 y 100. No se presenta
ese contador como tokens ni como consumo facturado por Codex.

El plazo del objetivo, entre 1 y 3600 segundos, incluye la espera de sus hijos.
Cada hijo tiene además un presupuesto `RunBudget` de pasos y tiempo. Vencer o
cancelar solicita interrupción, conserva como incierto un efecto ya despachado
y espera sus finalizadores y su recibo durable. Una función que suprima
cancelación no transforma un retorno tardío en efecto comprobado.

Los locks por sesión y las lanes de ejecución se conservan durante la limpieza.
El padre permanece registrado como propietario mientras cualquier hijo o su
persistencia siguen sin asentarse. La vista distingue `status`,
`parent_settled`, `parent_outcome` y `settled`; una respuesta de Cancelar o un
evento final del objetivo no bastan para liberar la interacción.

## Persistencia y ausencia de replay

Los eventos tipados `coordination.created`, `coordination.child` y
`coordination.finished` viven en el SQLite existente del estado; los recibos y
mensajes de cada hijo conservan su identidad. No hay una segunda base olvidada
por el respaldo. Los temporales usan únicamente el SQLite de memoria.

El `request_id` del objetivo propio también identifica su run y goal.
`delegations.lookup` permite encontrarlo después de perder una respuesta sin
repetir el arranque. Una consulta no establece retrospectivamente el ACK perdido.
`delegations.list` permite redescubrir objetivos de la sesión tras reiniciar.
Los runs activos encontrados al arrancar se conservan como interrumpidos; no se
reenvía el mensaje ni se recuperan permisos, cuentas o conexiones de sus hijos.

Las vistas son previews acotadas: hasta 64 KiB por objetivo, respuesta de cada
hijo hasta 2048 bytes y hasta ocho recibos con salida abreviada. La lista limita
la respuesta a 256 bytes y omite los recibos de detalle. Se conservan tamaño,
hash, contador, indicadores de truncado y presencia de efectos inciertos.
`runs.get` mantiene los recibos originales. Texto del hijo tiene procedencia
`model`; no se convierte en una verificación independiente.

## Cola y corrección de una ejecución

`runs.enqueue` admite hasta cuatro sucesores en cola por sesión y conserva su
identidad de solicitud. La siguiente entrada espera limpieza y persistencia
durable de las anteriores antes de preparar contexto o volver a usar su hilo.
Cancelar una entrada en cola nunca la envía al proveedor. Reiniciar no reproduce
la cola anterior. El evento de cola fija sus predecesores: si el resultado anterior
es incierto, falta su recibo o no se confirma su limpieza, el sucesor falla antes de
enviar inferencia. Conserva un evento causal con el ID y digest del recibo anterior.
Hace falta otra entrada explícita del dueño para continuar después de observar
esa incertidumbre; el prompt que ya estaba en cola no se convierte en un reintento.

`runs.steer` y `delegations.steer` son controles explícitos del dueño sobre un
run/hijo exacto. Registran primero una intención durable con ID, digest, hilo y
turno, y envían una sola petición oficial `turn/steer` con `expectedTurnId` y
`clientUserMessageId`. Un turno distinto se rechaza; un ACK perdido o discordante
queda sin confirmar y no se reintenta. El texto no concede permisos. Una fase de
aprobación o limpieza no admite esa corrección.

El texto y los recibos de corrección también pasan al contexto histórico acotado,
incluidos compacción, nueva conexión y rewind. Un recibo ausente conserva estado
`unknown`. Se inyectan como datos de usuario, con `instructions_current=false`,
`historical_ack_established=false` y `permits_replay=false`. Un rewind conserva
ese registro como auditoría, sin convertir la corrección posterior al corte en
una nueva instrucción ni un permiso. Si los datos esenciales no caben, se rechaza
la operación antes de publicar en vez de omitirlos silenciosamente.

Se verificó el contrato en el schema generado localmente por el cliente oficial
`codex-cli 0.163.0`, con HOME propio sin cuenta:

- `TurnSteerParams.json` SHA-256
  `857e7a2b061ae46ed6aedea6a5fddd3a6ce14f0334bf7eafaf10d4cbe68ec78b`.
- `TurnSteerResponse.json` SHA-256
  `866ba9a77c12b5d570c837d58f73bf40b24e50fa93404bdf792dd984d313b1aa`.

## Verificación

`test_delegation.py` usa servicio, socket Unix, estado SQLite, sesiones y autoridad
reales con un proveedor propio sintético. Comprueba dos hijos correlacionados,
historial y permisos separados, aprobación de delegación y otra decisión para
la escritura hija, rechazo, cancelación con limpieza retenida, vencimiento,
presupuesto, cola, aislamiento entre padres, lista paginada, reinicio y
temporales. Los previews conservan las respuestas originales fuera del resumen.

`test_runtime_budgets.py` comprueba las llamadas reales contadas, concurrencia,
plazos y supresión maliciosa de cancelación. `test_codex_steering.py` usa un peer
stdio propio y comprueba que el texto de corrección cambia la respuesta del
turno exacto; identidad ajena, turno cambiado y ACK discordante no se convierten
en una corrección confirmada ni en otra petición.

`DelegationIntegrationTests.swift` añade tres recorridos Swift → RPC firmado →
Python/SQLite: dos hijos con escritura aprobada y bytes comprobados
independientemente, cancelación con finalizador retenido y salida abrupta del
proceso de host propio seguida de arranque y observación de interrupción. El
rearranque y la consulta del mismo request no ejecutan otro hijo. Los tres
pasaron con cero fallos; el log local es
`/private/tmp/jarvis-core-delegation-native-tests.log`. No es un gesto GUI ni una
prueba con una cuenta externa: los peers y las decisiones de tests son sintéticos.

`ConversationQueueIntegrationTests.swift` pasó cuatro casos nativos adicionales:
selección del primer run activo frente a dos sucesores en cola, aprobación visible
del segundo, retención del tercero hasta confirmar el recibo SQL y bloqueo de
sucesores ante cancelación o un efecto incierto. También valida las identidades
y estados de las respuestas de cola/corrección. El fixture retiene únicamente
la publicación SQL de una ejecución completada; un ajuste previo retenía también
una ejecución bloqueada y fue corregido. El log del resultado final es
`/private/tmp/jarvis-core-conversation-queue-native-tests.log`.

Estas pruebas no se presentan como una conexión ChatGPT nueva, medición de coste,
multitud distribuida de agentes, certificación de todos los harnesses o revisión
completa de OpenClaw. El registro integrado de núcleo y sesiones identifica las
comprobaciones nativas y los controles finales del incremento.
