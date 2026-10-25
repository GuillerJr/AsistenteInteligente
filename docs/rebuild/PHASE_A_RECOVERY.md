# Fase A: respaldos, restauración preparada y diagnóstico

Implementación propia con contratos verificados y un recorrido nativo acotado con
decisiones reales del dueño sobre datos sintéticos. Este registro no acredita el cierre de
A-69 (onboarding/doctor), A-71 (recuperación) ni de la fase A. La activación de un
perfil restaurado, las reparaciones y la distribución permanecen pendientes.

## Referencia y separación del producto

Los lotes REA 13 y 14 estudian, por separado, comandos de respaldo/restauración,
verificación, bloqueo de mantenimiento e internals de snapshots del pin
`1e3ff306abaff29f03e7668d53b01c07f10294ac` de OpenClaw. Sus fuentes, hashes,
Evidence IDs y límites están en [REA_COVERAGE.json](REA_COVERAGE.json).
Se derivan contratos de comportamiento; no se importa ni ejecuta su implementación.
El total de quince lotes y 60 rutas TS/JS mantiene cobertura semántica parcial.

Una observación de esa referencia es especialmente relevante: algunas operaciones
de listado/resolución también resuelven marcadores pendientes. Jarvis separa el
diagnóstico de solo lectura de cualquier recuperación o reparación.

## Contrato implementado

La app prepara una revisión inmutable del perfil físico, generación, recursos y
destino. Una operación de respaldo o restauración solicita su propio permiso a
la misma `ApprovalAuthority` que usa el resto del servicio, en un dominio de host
tipado. No inventa una conversación, herramienta del modelo ni autoridad adicional.
El permiso está ligado al propósito, operación y argumentos; no se transfiere
entre chats, mantenimiento o perfiles y no renueva el plazo tras aprobar.

La admisión del perfil incluye todas las RPC anteriores, también consultas que
pueden inicializar SQLite de forma diferida. El corte exige que no queden
escritores, ejecuciones, asentamiento de recibos, procesos, programador, Telegram,
vinculación o recursos de cierre sin confirmar. No los detiene automáticamente.
El worker conserva la barrera y su propietario hasta terminar realmente, incluso
si se cancela repetidamente la tarea que lo espera.

Las conexiones ya poseídas se copian a RAM mediante la API online backup de
SQLite; la fuente no recibe checkpoint, VACUUM ni migración para preparar una
copia. La validación exige esquemas propios, integridad, referencias y hashes.
La carga de extensiones SQL se deniega también cuando CPython fue compilado sin
el método opcional `enable_load_extension`; no se carga ninguna biblioteca para
comprobar disponibilidad y no se ignoran fallos de los controles presentes.
No existe una transacción SQLite que abarque todos los almacenes: la coherencia
entre ellos depende del corte del host y sus comprobaciones, no de copiar archivos
WAL sueltos ni de usar `immutable=1` sobre una familia viva.

Los recursos portátiles son estado persistente, memoria, tareas, perfiles,
manifiestos de skills y registro de Telegram. La configuración se genera desde
cero, sin bindings ni comandos MCP. Se excluyen credenciales, estado del proveedor,
transporte, permisos vivos, procesos/audio, chats y memoria temporales, archivos
de workspace y recursos externos de skills. El journal del propio mantenimiento
también queda excluido para evitar copia circular o replay de operaciones.
El contenido escrito por el usuario en chats/memoria puede contener secretos:
no se promete detectarlos o eliminarlos de forma general.

Los archivos nuevos son privados, con nombres fijos y límites de recursos. Se
verifican antes de una publicación nativa que falla si el destino ya existe.
No hay fallback que pueda sobrescribirlo. Un fallo de fsync, pérdida de ACK,
revocación o cancelación tras despachar la publicación conserva la incertidumbre;
no elimina ni repite automáticamente ese resultado.

Restaurar crea exclusivamente un perfil nuevo y desarmado. Borra threads/bindings,
no conserva permisos ni transporte y deja tareas, canales y skills deshabilitados.
Los recibos anteriores siguen siendo históricos; no acreditan efectos actuales.
`restore_record.json` enumera exclusiones y pasos todavía necesarios. No se abre
sesión, inicia un programa MCP, envía mensajes ni activa el perfil resultante.

El diagnóstico devuelve códigos, versiones, permisos, cantidades e integridad,
sin texto de chats, memoria, SQL, credenciales o argumentos de programas. Sobre un
perfil activo consume imágenes RAM del mismo corte; no abre un store para
fabricar disponibilidad. Una fuente existente que aún no se posee puede quedar
bloqueada. Ningún informe aplica reparaciones o resuelve `.pending`.

## Superficie y recibos

Las peticiones largas devuelven un ID de operación para consultar el progreso.
La UI utiliza `maintenance.status` y `maintenance.pending`, sin iniciar inferencia
durante la supervisión. No reenvía una mutación tras un timeout. Cancelar señala
una operación concreta y mantiene su propietario hasta asentamiento.

| Métodos | Efecto |
| --- | --- |
| `backup.review`, `restore.review` | Preparación de metadata; no crean el destino. |
| `backup.create`, `restore.stage` | Solicitan permiso propio y ejecutan el destino exacto aprobado. |
| `backup.verify`, `doctor.report` | Lecturas acotadas; no reparan ni activan nada. |
| `maintenance.status`, `maintenance.pending` | Supervisión del coordinador. |
| `maintenance.decide`, `maintenance.cancel` | Decisión nativa del dueño o cancelación de la operación exacta. |

El journal privado conserva resultados y errores de limpieza por separado. Leer
un registro inconcluso lo presenta como desconocido sin reejecutarlo ni escribir
una recuperación. No se expulsan registros sin resolver para admitir más efectos.
Los identificadores físicos de 64 bits y las fechas en nanosegundos viajan como
texto decimal para que el transporte Swift no pierda precisión.

## Verificación y pendientes

Las pruebas usan bases SQLite y archivos sintéticos reales, autoridad central y
workers coordinados. Cubren roundtrip, esquema/FTS, corrupción, cambios físicos,
denegación, plazos originales, cancelación, publicación incierta, limpieza,
precisión de identidades y fallo del journal. Las aprobaciones de fixtures no
sustituyen una decisión real del dueño en la app.

Los controles locales de Python, Swift, lint, bytecode y paquetes están aprobados.
El recorrido Swift → RPC → servicio → SQLite acredita diagnóstico, respaldo,
verificación y staging restaurado con decisiones sintéticas; no usa proveedor.
En la app se observó el diagnóstico del perfil sintético: almacenes y referencias
correctos, con skills ausentes. Se reprodujo y corrigió la retención de una revisión
al cerrar Ajustes: el ciclo real cerrar/reabrir libera el chat, conserva su borrador
sin enviarlo y descarta la revisión local anterior. Los eventos se ligan a la ventana
concreta; el cierre conserva los trabajos o respuestas que siguen sin confirmar.
El checkpoint `a6e08f3` pasó Python (1538 casos), Swift (161 casos), lint, bytecode,
paquetes y CI en Ubuntu 24.04 y macOS 26. El bundle de desarrollo observado tiene
7.656.400 bytes y SHA-256
`78abb7cc3795abf6007a60fd700fd545f1961206377332b623d3c548894f4a78`.
Utiliza Python y Codex externos y firma ad hoc; no sustituye la app instalada.

En ese bundle el dueño autorizó una copia del perfil sintético y, después de
verificarla desde Jarvis, autorizó preparar un perfil nuevo. La app presentó los
recibos «Completada» y «Perfil preparado y desarmado». El manifiesto observado fue
`e52f6d7e1dcc9d49257e2a3a0b5b31f67451b395e1e90f09170647a8f4997bf3`.
La verificación independiente del respaldo confirmó seis roles presentes, skills
ausente explícitamente, hashes/formato coincidentes, directorios 0700, archivos
0600 y ausencia de `.pending`. Esto acredita el artefacto y su recibo, no la
autenticidad de un firmante ni los efectos de herramientas externas.
Los identificadores de operación y el alcance de la evidencia están en el
[registro de cualificación](PHASE_A_RECOVERY_QUALIFICATION.json). Al cerrar y
reabrir Ajustes se conservó el recibo; después el chat quedó disponible con el
mismo borrador sin enviar.

El recorrido no acredita todavía denegación, revocación, cancelación y pérdida
de respuesta reales en toda la interfaz. También siguen pendientes la exportación del host de fuentes frías,
incluidas WAL, la inclusión de archivos y
artefactos del workspace, activación/reconciliación de memoria y canales, reparación
explícita, migraciones y distribución autocontenida. Son trabajo de la fase A;
no se trasladan a B para declarar una paridad inexistente.
