# Fase A: activación local y apertura de perfiles restaurados

Implementación propia integrada, con 1.754 pruebas Python y 175 nativas aprobadas.
La revisión de sólo lectura se observó en la app; todavía falta la aprobación real
de activación y la apertura explícita del perfil sintético. No cierra A-69, A-71
ni la fase A. La fase B sigue pendiente.

La referencia se estudió mediante REA por partes, incluidos los contratos de
respaldo y cierre de los lotes 13–16. No se copia ni ejecuta OpenClaw.

## Contrato de la ampliación

Restaurar sigue creando un perfil nuevo y desarmado. Activar añade una admisión
local mediante `profile.restore.review` y `profile.restore.activate`; no inicia
ese perfil ni abre una conexión. La tarjeta autoriza sólo publicar la admisión y
aclara que el perfil actual todavía no cambia. Usa la misma `ApprovalAuthority`,
el dominio tipado `profile.restore.activate`, el perfil/generación actuales y los
argumentos exactos de la revisión; no se crea una sesión ficticia ni una segunda
autoridad. El análisis y la cola consumen el plazo original, sin renovar el TTL
al esperar la decisión. El destino físico no puede ser el perfil host actual.

La revisión fija raíz e identidad física, marcador, procedencia, hashes y
cantidades. Lee el perfil preparado sin construir sus stores ni crear su lock.
La publicación conserva `restore_record.json` y añade `restore_admission.json`,
ligado a esa raíz física. La admisión no es permiso de herramienta, conexión de
proveedor ni prueba de autenticidad. Los hashes iniciales de stores se comprueban
para la publicación; no impiden sus futuras mutaciones legítimas.

Una admisión completada deja el estado «Activado; sin abrir». **Abrir perfil
activado es siempre otro paso explícito del dueño**, incluso con recibo durable.
El cliente confirma el cierre del servicio anterior y crea otro contexto de app,
clientes, borradores, voz y autoridad. El bootstrap recibe cinco restricciones
all-or-none: dispositivo, inode, hash de marcador, hash de admisión e ID de la
operación de admisión. Se validan antes de lock, stores, proveedor o IPC; después
se adquiere el flock real. Esas restricciones no conceden permisos ni eliminan
la posibilidad de rechazar un perfil ocupado o cambiado.

El nuevo workspace es privado y vacío. La memoria existente conserva sus scopes;
**no se reasocia automáticamente al nuevo workspace**. Un resultado vacío en el
scope nuevo no demuestra pérdida de datos ni acredita reconciliación de memoria.
Tareas, rutas de Telegram y skills siguen deshabilitadas; no se importan programas
MCP, transporte o permisos vivos. No se añade un selector persistente de perfiles.

## Incertidumbre, inspección y limpieza

ActivationReceipt es distinto de RecoveryReceipt. Distingue admisión local
publicada, intento sin activación y publicación desconocida. El journal tipado
no guarda Owners, descriptores, credenciales o permisos. Una pérdida de ACK,
fallo de fsync o cancelación después de intentar publicar conserva el recibo
`unknown`; no se repite la publicación ni se cambia a `completed` por observar
posteriormente un archivo válido.

`profile.restore.inspect` sólo comprueba el estado local actual. `maintenance.cleanup`
continúa únicamente la limpieza del Owner retenido, sin publicar de nuevo ni
crear otro permiso. Un Owner vivo impide nuevo mantenimiento y apertura. La tarea
y el lease se conservan hasta terminar realmente el worker, también ante
cancelaciones repetidas. Restos actuales ambiguos de activación o un lock inseguro
se rechazan; no se borran automáticamente.

Tras reiniciar, un registro histórico desconocido no se reconstruye como Owner
ni bloquea para siempre una selección nueva. La inspección de una pareja actual
válida puede permitir una apertura explícita nueva, sin atribuir a la operación
anterior un fsync, ACK o cleanup que no quedaron acreditados. El bootstrap y el
flock posterior siguen siendo obligatorios; inspeccionar no prueba disponibilidad.

## Elección local del proveedor

El primer arranque restaurado informa estado local y desconectado: sin SDK,
`account/read` o ejecución de modelo. Se rechaza el override de estado del proveedor
anterior y sólo se deriva el directorio propio del nuevo perfil. No se lee
`auth.json` ni se decide conexión por encontrar credenciales.

«Conectar» conserva primero una intención local no portable, ligada a la raíz y
admisión; después puede iniciar el cliente oficial. Durante una transición se
bloquean nuevos runs y consultas al proveedor. Cada llamada/turno comprueba su
epoch tras esperas y antes de despachar datos o efectos. Una elección propia
válida permite consultas oficiales en arranques posteriores sin imponer otro
login por sistema. Logout detiene consultas localmente; si el cierre remoto
falla, informa que no quedó confirmado y no afirma haber borrado la cuenta.
La limpieza de un cliente ya poseído sigue disponible aunque falle el control.

Doctor reconoce la admisión validada mediante lecturas acotadas. Backup mantiene
la allowlist de `jarvis-backup/1`: ni marcador, admisión ni elección local se
exportan. Otro restore genera procedencia nueva y exige su activación propia.

## Verificación acotada

El árbol integrado pasó Ruff, bytecode, pureza de base y 1.754 pruebas Python en
108,63 s. La suite Swift pasó 175 casos con cero fallos en 29,839 s, incluidos dos
recorridos nativo → RPC real → SQLite con decisiones sintéticas y cero llamadas
al proveedor del destino. La ampliación añade 169 casos Python y 13 nativos.

El bundle de desarrollo compiló y pasó plist y firma ad hoc estricta. Su ejecutable
mide 8.485.360 bytes y tiene SHA-256
`553ba3ecd8827b7fcc57134555b71b7d82903508a84aa35b7106174e834fff64`.
Requiere Python y Codex externos; no sustituye la app instalada del dueño.

En ese bundle se observó la pantalla «Perfil restaurado» y la revisión exacta del
perfil sintético previamente preparado: los siete roles se presentaron, con skills
ausente. Descartar la revisión y cerrar Ajustes devolvió el chat original con
«BORRADOR RECUPERACIÓN SIN ENVIAR», sin enviarlo. No se solicitó ni aprobó activación
en este preflight. Los controles de publicación y CI se registran por separado.

Faltan la decisión real del dueño y «Abrir perfil activado», el cierre/reinicio
local sin cuenta, y los casos reales de denegación, cancelación y resultado incierto.
Los fixtures cubren estos estados pero no sustituyen el recorrido nativo del dueño.
Los borradores duraderos anteriores se retienen sólo en RAM durante esta sesión;
no hay selector para recuperarlos. Chats y borradores temporales no se trasladan.

Las lecturas tienen límites de bytes y checkpoints, pero no interrumpen una llamada
bloqueada en el kernel. La barrera es cooperativa, sin aislamiento frente al mismo
UID. Siguen pendientes WAL frío/journals, archivos de workspace, reconciliación y
reactivación de memoria/canales, reparación, migraciones y distribución autónoma.
Ver [recuperación](PHASE_A_RECOVERY.md) y el bloque `activation_followup` del
[registro de cualificación](PHASE_A_RECOVERY_QUALIFICATION.json).
