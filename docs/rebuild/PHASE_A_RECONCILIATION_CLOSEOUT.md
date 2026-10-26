# Activación, copia de memoria y reconciliación de canales

Este incremento completa el contrato local de esos tres recorridos sobre perfiles
restaurados. La cualificación usa datos propios sintéticos. El dueño delegó
expresamente las aprobaciones de esos perfiles al agente; esa decisión no concede
acceso a credenciales, TCC, historial ni perfiles personales.

No se incorporó código de OpenClaw. Este trabajo continúa los contratos propios de
[activación](PHASE_A_ACTIVATION.md), [memoria](PHASE_A_MEMORY_RECONCILIATION.md) y
[recuperación](PHASE_A_RECOVERY_NATIVE_CLOSEOUT.md). No se atribuye una nueva
partición REA a la revisión del código propio.

## Activación y apertura

La admisión y la apertura siguen siendo decisiones distintas. Las pruebas amplían
los negativos con un flock adquirido después de revisar, la sustitución física del
destino y cancelaciones repetidas después de intentar publicar. El mismo corte
permanece retenido hasta cerrar realmente su propietario; una limpieza no publica
de nuevo ni transforma un resultado incierto en éxito.

La prueba nativa descubrió que el aviso podía mostrar «caducó» y conservar los
botones después de terminar el sondeo. Los controles ahora tienen una presentación
con plazo propio, ligada al menor vencimiento de revisión y ticket. No modifica el
TTL, consulta la red, renueva permisos ni sustituye la comprobación de autoridad.
La revisión de activación también comprueba su propio vencimiento al decidir.

El primer corpus no vacío encontró otro fallo en la prueba: una tarea sintética
usaba un identificador de workspace sin el formato de la interfaz. Una regresión
reprodujo el rechazo exacto antes de corregir exclusivamente el generador y crear
un corpus nuevo. Se conservan la observación fallida y sus originales. La nueva
prueba lee los catálogos nativos, conversaciones y mensajes, además de comprobar
la identidad local; la apertura no se acredita sólo por iniciar un proceso.
El diagnóstico nativo distingue qué lectura no pudo validar y conserva el cierre
protector, sin atribuir cualquier error de catálogo a una identidad incorrecta.

## Memoria fría y copia explícita

Inventario y revisión admiten familias SQLite DELETE, WAL y journal íntegramente
validadas mediante copias privadas. Fijan identidad física, contenido, generación,
workspace y límites antes de ofrecer una decisión. Las familias ambiguas o dañadas
se rechazan; este recorrido no repara un prefijo WAL ni abre el original para
intentar hacerlo legible.

La metadata incluye `source_kind` y `cold_sqlite_recovery_required`. La aprobación
puede permitir a SQLite recuperar/checkpoint sus auxiliares WAL/journal. Antes de
aprobar se verifica la conservación física; después de abrir explícitamente se
comprueba la conservación lógica de las notas originales. Cancelar tras abrir no
promete conservar los auxiliares byte por byte. DELETE también puede crear su
journal transaccional normal.

La copia conserva texto y procedencia, crea IDs nuevos con revisión 1 y excluye
ámbitos ligados a sesión. El destino del mismo agente debe estar vacío. Migración,
notas, chunks, generación y recibo SQL comparten transacción. Un COMMIT cuya
confirmación se pierde conserva `unknown`; consultar el registro no acredita el
ACK anterior ni permite repetirlo. La custodia y el mismo corte permanecen
retenidos si la limpieza no queda confirmada.

## Rutas restauradas de Telegram

El único canal elegido sigue siendo Telegram privado. Las rutas restauradas
necesitan estos pasos independientes:

1. Revisar y autorizar localmente el cambio de una ruta concreta mediante
   `telegram.restore.review` y `telegram.restore.reconcile`. Se puede habilitar o
   deshabilitar, conservando agente, sesión e historial. El bot histórico es una
   etiqueta local, no una cuenta externa verificada.
2. Conectar explícitamente el mismo bot. No se importan token, conexión ni permisos.
3. Completar un enlace privado nuevo, con nonce y claim actuales. Si el chat ya
   tiene ruta, `telegram.restore.pairing.confirm` conserva la conversación y registra
   únicamente una atestación RAM de su bot, agente/revisión, sesión, ruta/revisión,
   configuración y workspace físico. No inicia el canal.
4. Activar el canal por separado. Inicio, polling, despacho y envío comprueban de
   nuevo esa atestación. Desconectar, reconciliar o cambiar las identidades que la
   ligan la invalida. Un reinicio nunca importa la prueba anterior.

Se descarta conservadoramente el segundo parcial del inicio: un mensaje anterior
con la misma fecha de un segundo tampoco se acepta como entrada nueva. Los
registros históricos, incluidos controles y eventos interrumpidos, no se reproducen.

La reconciliación usa la autoridad central de mantenimiento y un recibo durable
propio. El CAS final vuelve a comprobar los hermanos del perfil y permite sólo el
temporal propio fijado. La publicación JSON revisada conserva la custodia de sus
descriptores y locks desde su adquisición. Si no se puede cerrar, conserva el Owner y el corte; la limpieza
posterior retira únicamente esos recursos, sin repetir rename ni cambiar un recibo
incierto. `telegram.restore.inspect` informa el estado actual con
`permits_replay=false`; no confirma retrospectivamente una publicación.

La app presenta las rutas pendientes de enlace fresco y permite detener un lector
que aún conserva un poll mientras su prueba deja de ser válida. Actualizar las
filas después de un resultado asentado es sólo lectura; no reenvía la operación.

## Verificación y límites

Pasaron **2402 pruebas Python y 305 Swift**, Ruff, bytecode, pureza de la base,
paquete fuente/wheel e instalación aislada sin red. El bundle final compiló y
pasó firma ad hoc estricta. Los hooks vuelven a calificar el árbol congelado al
hacer commit/push; estos resultados locales no anticipan la CI del commit.

Se registran 14 casos nativos de operación: negativos y admisión/apertura de
activación; rechazo y copia DELETE; rechazo, habilitación y revocación de ruta;
y caducidad, cancelación y copia WAL. Ambos perfiles volvieron a abrirse con el
bundle final: una sola copia SQL por perfil, rutas revocadas, cuenta desconectada
y cero permisos vivos. Se conservan los primeros recorridos fallidos, la
regresión de catálogo y las expectativas históricas ajustadas al nuevo contrato.

El [registro integrado](PHASE_A_RECONCILIATION_CLOSEOUT.json) identifica los corpus,
fuentes, bundles, operaciones, oráculos, resultados y fallos previos. Distingue:

- Pruebas de motores y autoridad con decisiones sintéticas e inyección de fallos.
- Integración Swift → RPC local → servicio/SQLite/JSON reales con peers sintéticos.
- Gestos CUA sobre la app de desarrollo, aprobados por el agente bajo la delegación
  explícita del dueño, y comprobaciones independientes sobre esos mismos datos.

Los bots/claims de los nuevos tests de vinculación son sintéticos. No se conectó
una cuenta externa ni se envió historial a Telegram para calificarlos. Eso no
sustituye la cualificación prolongada del canal Telegram, voz, web o TCC, que tienen
alcance propio. Tampoco acredita resistencia frente a otro proceso malicioso del
mismo usuario, autenticidad de una distribución o durabilidad física del hardware.

El bundle sigue usando Python y Codex externos. La instalación anterior y los
perfiles personales permanecen independientes. A-71 global, la distribución
cualificada y la fase A siguen abiertos; iPhone permanece aplazado y la fase B no
ha comenzado. Este cierre local no marca las 80 familias como terminadas.
