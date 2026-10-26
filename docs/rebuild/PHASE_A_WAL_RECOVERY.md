# Fase A: recuperación WAL sobre copias privadas

Primer incremento propio conectado a respaldo y diagnóstico. Su cualificación
automática usa únicamente datos y autorizaciones sintéticos. Se comprobaron el
diagnóstico, respaldo, verificación y restauración en la app con perfiles sintéticos
y aprobaciones reales del dueño. No se sustituye la app anterior ni se declara completa la fase A.
La fase B no ha comenzado.

## Alcance implementado

Los tres roles propios —estado, memoria y tareas— admiten familias frías con main
WAL, un WAL no vacío y SHM opcional, sin journal. La revisión fija identidades y
ausencias de los cuatro miembros y sólo lee metadatos y cien bytes de cabecera.
Rechaza tamaños físicos inadmisibles antes de ofrecer la aprobación. El worker
comprueba después el contenido completo, generaciones y presupuesto lógico.

`cold_wal.read_wal_image` fija los descriptores originales y compara sus hashes
en dos pasadas. Copia únicamente main y WAL a un directorio propio privado;
comprueba también el hash de la copia. SHM original se valida, pero no se utiliza:
SQLite reconstruye el índice únicamente en el staging. SQLite nunca abre el
original y no se ejecutan checkpoint, migración ni reparación sobre él.

El preflight propio verifica cabeceras, tamaño de páginas, todos los frames,
salts y checksums de ambos endianness. Debe existir al menos un commit válido.
Una cola completa válida sin commit se conserva en la copia privada; la API
online backup de SQLite obtiene sólo el último estado confirmado. La revisión
nativa muestra esta política. No se trunca ni ignora una cola ambigua para
declarar éxito: truncación, corrupción, generaciones mezcladas y ausencia de
commits producen error explícito.

La imagen RAM se normaliza a rollback sólo después de obtenerla mediante backup
nativo, conforme a la [API de deserialización](https://www.sqlite.org/c3ref/deserialize.html).
El inspector existente valida esquema propio, versión, integridad, relaciones,
texto y límites. Se deniega la carga de extensiones. La fuente privada se abre
con `mode=ro`; los únicos sidecars nuevos pertenecen a ese staging.

## Límites y custodia

El presupuesto admite por defecto como máximo 64 MiB de familia por rol y
128 MiB agregados. Incluye main, WAL y SHM, imágenes ya materializadas, tamaño
lógico del último commit y una reserva conservadora de reconstrucción del índice.
La ocupación privada real se vuelve a comprobar. Las conexiones ya poseídas se
presupuestan por páginas lógicas, sin suponer que todo está en main. Son límites
de payload y staging, no una medición ni garantía de RSS total del proceso.

El checksum tiene límites propios de frames y trabajo; el inspector conserva sus
límites SQL. Cancelación, deadline y barrera se comprueban durante adquisición,
verificación, backup y validación. Una llamada del sistema bloqueada sigue sin
ser interrumpible por un timeout Python: el worker conserva su propiedad hasta
terminar realmente.

`WalCaptureOwner` se registra antes de adquirir recursos. Conserva conexiones,
descriptores e identidades privadas hasta confirmar cierre y retirada. Un cierre
incierto nunca autoriza cerrar otra vez un número de descriptor posiblemente
reutilizado. Una creación o identidad ambigua tampoco autoriza adoptar o borrar
un archivo por su nombre. No se elimina recursivamente una ruta desconocida.

`CapturedProfileCut` retiene el mismo corte mientras quede ese propietario. El
contexto de autorización sale siempre en su Task original. `maintenance.cleanup`
y el cierre del servicio sólo retiran los recursos de ese mismo propietario,
sin grant nuevo, otra captura, COMMIT o publicación. Un fallo antes de crear el
respaldo conserva `no_published_effect` y distingue su limpieza pendiente.

Tras retirar los recursos, la fase termina en fallo/cancelación y el recibo
histórico permanece intacto, incluida su evidencia original de limpieza. El
estado vivo del propietario se publica por separado. El diagnóstico lector
conserva esa custodia en RAM; no fabrica un recibo durable ni escribe un journal
de efecto. Una muerte abrupta del proceso puede dejar staging privado: no se
afirma reclamación automática ni se borran esos restos al reiniciar.

## Cliente nativo

El Store de mantenimiento ahora reside en DesktopCore y se prueba directamente.
La interfaz diferencia worker asentado, propietario pendiente y recibo histórico.
Cerrar Ajustes, perder una respuesta, cambiar conexión o recibir un ACK de
cancelación incoherente no libera la custodia ni vuelve a enviar la operación.
Una lectura sin ACK queda bloqueada hasta establecer su cierre; no se convierte
en prueba de que no haya recursos pendientes.

La revisión explica «sólo transacciones confirmadas» y ofrece «Completar limpieza»
únicamente para la operación conocida con propietario retenido. Conversación,
reconexión y cambio de perfil conservan sus barreras. La reconciliación mutable
de memoria y su DTO siguen admitiendo sólo el contrato DELETE anterior; este
soporte lector WAL no concede permiso para copiar ámbitos sobre una fuente WAL.

## Evidencia y límites

El [registro de cualificación](PHASE_A_WAL_RECOVERY_QUALIFICATION.json) conserva
identidades, hashes, casos y tipo de evidencia. Las suites integradas pasaron
1980 pruebas Python y 223 Swift. Los casos nuevos son 45 de núcleo y 21 del host;
Swift añade 14 de custodia y dos recorridos IPC WAL a los anteriores.

Los fixtures producen tres commits en un proceso propio que termina antes de
adquirir el perfil. El marcador final sólo existe en WAL. El recorrido
Swift→IPC→servicio→SQLite cubre diagnóstico, respaldo, verificación y restauración;
consulta los títulos finales con SQLite RAM independiente y compara hashes,
identidades, permisos, tamaños, mtime y ctime de todos los originales. Otro
recorrido ejercita el Store real con limpieza incierta. No llaman al proveedor
ni inicializan stores de las fuentes para recuperarlas. Las aprobaciones de esos
recorridos son de fixture, nunca decisiones del dueño.

La publicación `8bd1641` aprobó pre-commit, pre-push, paquetes, bundle nativo y CI
en macOS y Linux. Un control posterior detectó que el sdist incluía los tests WAL
pero omitía su helper. El manifiesto ahora incorpora todas las fuentes Python de
pruebas y el guard compara sus rutas y bytes exactos con el árbol cualificado.
Dos regresiones reproducen la omisión y un helper obsoleto: antes fallaban y
después pasaron junto a los otros 34 casos del guard. Las 66 pruebas WAL también
pasaron desde el sdist extraído, verificando que módulo y helper procedían de
ese árbol; no cambiaron las fuentes del runtime ni las nativas.

El [recorrido nativo acotado](PHASE_A_WAL_RECOVERY_LIVE.json) verificó el diagnóstico
de un perfil sintético con memoria WAL y estado/tareas DELETE. Los tres recursos
resultaron correctos; el informe global fue parcial por agentes, skills y Telegram
ausentes y relaciones no acreditadas. Los cinco miembros SQLite mantuvieron
bytes, identidad, permisos, tamaño, mtime y ctime.

La revisión visual detectó una colisión de identidad entre la fila de memoria y
su aviso WAL que no cubrían los contratos. Se separaron esas identidades y se
añadieron dos regresiones estables, incluidas en 225 pruebas Swift aprobadas.
La app corregida muestra ambas filas y el aviso completo; se comprobó mediante
accesibilidad y captura visual. El intento de XCTest con AX devolvía una vista
vacía y se retiró: no se usa como evidencia visual. Esas revisiones visuales se
descartaron antes de pedir aprobación.

Después de publicar `8c7e7bc`, con 1982 pruebas Python, 225 Swift, paquetes, bundle
y CI macOS/Linux aprobados, el dueño autorizó el respaldo desde la app. Una
solicitud anterior había caducado sin recibo ni destino; se confirmó su cierre
antes de preparar otra revisión. La operación autorizada terminó con publicación,
recibo durable y sin recursos pendientes. La verificación nativa posterior también
terminó correctamente, sin restaurar ni activar otro perfil.

Un verificador independiente comprobó el manifiesto y los hashes de los cinco
recursos publicados. Deserializó únicamente los bytes del respaldo en SQLite RAM:
la nota conserva el título del tercer y último commit, su cuerpo y procedencia
sintéticos, con integridad y relaciones correctas. Los cinco miembros originales
conservan bytes, identidad, permisos, tamaño, mtime y ctime. El perfil de esta
comprobación tenía sólo memoria WAL; estado y tareas eran DELETE. El recorrido
acotado no acredita todas las variantes WAL ni autenticidad del contenido.

El 26-10 se [restauró ese respaldo desde la app](PHASE_A_WAL_RESTORE_LIVE.json),
con otra aprobación del dueño, a un perfil nuevo y desarmado. El verificador
independiente leyó únicamente bytes y SQLite RAM: conservó todos los datos lógicos,
la nota del tercer commit y sus IDs/ámbitos, y comprobó configuración vacía y
ausencia de tareas, permisos y conexiones activas. Los cinco originales y los seis
archivos del respaldo conservaron bytes y metadatos. El destino no se activó ni
se abrió como servicio.

Se aclaró la revisión: muestra el origen y hash del respaldo y etiqueta las cifras
del perfil activo como comprobaciones para conservarlo, sin presentarlas como
tamaños del respaldo. El caso IPC WAL existente ahora verifica también el contenido
de los tres SQLite restaurados y sus controles desarmados; pasó junto a la suite
de 225 Swift. No añade casos duplicados ni cambia el backend de restauración.

Siguen pendientes los journals de rollback, WAL vacío o sin WAL asociado,
recuperación de prefijos ante corrupción/reset antiguo, reparaciones, archivos
del workspace, reclamación tras muerte abrupta y la matriz real completa. Esas
capacidades permanecen dentro de A; no se trasladan a B.

La investigación que orienta el contrato es [OC-18](REA_PART18_SUMMARY.json).
Los algoritmos y contratos de Jarvis son propios; no se importa ni ejecuta
OpenClaw. La semántica del WAL se contrastó con el
[formato oficial de SQLite](https://www.sqlite.org/fileformat.html#walformat) y
la [API online backup](https://www.sqlite.org/c3ref/backup_finish.html).
