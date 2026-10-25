# Fase A: respaldos, restauración preparada y diagnóstico

Implementación propia con contratos verificados; recorrido real de la app pendiente. Este registro no acredita el cierre de
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
Quedan los recorridos desde la app con decisiones reales del dueño. También siguen pendientes la exportación del host de fuentes frías,
incluidas WAL, la inclusión de archivos y
artefactos del workspace, activación/reconciliación de memoria y canales, reparación
explícita, migraciones y distribución autocontenida. Son trabajo de la fase A;
no se trasladan a B para declarar una paridad inexistente.
