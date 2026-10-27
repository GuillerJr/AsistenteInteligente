# Memoria local: índice, recuperación y mantenimiento

Incremento propio, 27-10-2026. El dueño eligió MiniLM multilingüe compacto en
CPU; Codex con ChatGPT sigue siendo el proveedor de conversación. La lectura
de referencia está dividida en [OC-23A/B/C/D](REA_OC23_MEMORY.md), con blobs,
hashes y Evidence IDs. No se importa ni ejecuta implementación de OpenClaw.

## Datos y decisiones

La fuente son las notas que ya fueron guardadas con autorización en MemoryStore.
Una URI es una etiqueta de procedencia no verificada: no autoriza leer archivos,
historial, red o credenciales. No se exploran carpetas personales. Los esquemas
portátiles 1/2 de la memoria conservan sus datos y citas originales.

La política comienza con recuperación e indexación automáticas desactivadas y
modo léxico. Su revisión liga cliente autenticado, perfil físico, agente, carpeta,
revisión y digest. La confirmación caduca y vuelve a comprobar esos valores antes
de publicar. Los controles quedan fuera del perfil portátil; no se restauran como
permisos. Los chats temporales mantienen notas, índice, política y trabajos en RAM.

El modelo se prepara únicamente por acción explícita. Su [contrato y licencia](MEMORY_MODEL_RESEARCH.md)
fijan revisión, archivos y SHA-256. No hay respaldo automático a otro motor.
Estado y búsqueda no descargan nada. Un modelo ausente o inválido se informa.

Los embeddings se calculan localmente. Si se autoriza recuperación automática,
hasta cuatro fragmentos pertinentes acompañan el mensaje del turno a Codex con
ChatGPT. La interfaz explica esa diferencia antes de confirmar. Revocar o borrar
un recuerdo impide su siguiente recuperación; no elimina fragmentos enviados
anteriormente de las conversaciones que ya los recibieron.

## Índice y consultas

El índice SQLite derivado contiene identidades, hashes, revisiones, coordenadas
y vectores; el texto se toma siempre de un snapshot actual de la fuente. Es
prescindible y se publica por generaciones, con comprobaciones después de las
esperas y justo antes de sustituir la generación anterior. Su límite es 256 MiB.
Capacidad agotada, corrupción, cancelación o una generación obsoleta son estados
visibles; no se presentan como una indexación completa.

Las búsquedas disponibles son léxica, semántica local y combinada. La combinada
fusiona rangos independientes de tokens y similitud; la selección limita
fragmentos redundantes. Una puntuación representa relevancia, no veracidad.
Las citas conservan ID, revisión, hash y límites UTF-8 exactos, y se resuelven
contra la fuente vigente. La paginación semántica liga también consulta, política,
modelo, generación y fingerprint del índice.

El encoder usa 384 dimensiones y spans de hasta 128 tokens y 768 bytes con
solapamiento. Recorre el documento completo dentro de sus límites explícitos;
no resume silenciosamente el final ni promedia un documento entero. Las consultas
manuales superiores al límite se rechazan. La proyección del mensaje largo para
recuperación automática se declara en su estado.

La indexación automática observa las generaciones de las notas aprobadas,
agrupa cambios y mantiene una única construcción activa. No lee las URI ni
reimporta notas olvidadas. Reconstruir reutiliza vectores válidos cuando coincide
todo el contrato, o vuelve a codificar desde la fuente. La fuente no se repara
ni se descarta para acomodar una caché rota.

## Turnos, mantenimiento y cierre

Cada turno prepara una cápsula nueva de datos no confiables. Se entrega como
entrada separada del mensaje, también al reanudar un hilo, y no como instrucciones
de perfil, permisos o historial de Jarvis. La barrera de escritura del proveedor
revalida política y memoria antes del primer byte. Una corrección, eliminación,
cambio de cliente o revocación invalida la cápsula anterior.

El panel permite preparar/verificar el modelo, reconstruir/verificar el índice,
cancelar trabajos y retirar explícitamente una copia de modelo anterior. Una
reparación del modelo publica otra copia y conserva el original; el control admite
como máximo dos copias conocidas. La retirada exige identidad física registrada
y layout conocido: no adopta ni borra archivos desconocidos.

Los trabajos tienen ID y recibo; consultar el mismo ID no repite una mutación.
Un ACK perdido o una interrupción después de publicar conservan el resultado
incierto y bloquean la transición del perfil hasta resolver el cierre. El proceso
CPU tiene plazos, frames, entradas, CPU y salida acotados; no se afirma una cuota
RSS impuesta por el sistema operativo. En macOS su Seatbelt
deniega red y escrituras. Linux conserva límites de proceso y protocolo, sin
afirmar un aislamiento de red del sistema operativo que no se ha implementado.

El worker se retira entre peticiones al consumir su presupuesto acumulado de CPU;
el padre confirma su cierre antes de iniciar otro. Cerrar el servicio espera al
índice, trabajos, threads y worker. Un cierre incierto retiene la custodia y evita
abrir otro perfil sobre esos recursos.

## Cualificación

Pasaron 91 casos focales Python del bloque (11,27 s), 40 del supervisor (4,11 s)
y 49 de paquete/dependencias (4,94 s). Cada ejecución confirmó grupo terminado,
fuentes intactas y desmontaje/retirada de su cuota; sus máximos fueron 7.680.000,
5.025.792 y 5.742.592 bytes respectivamente. El protocolo del encoder pasó sus
25 casos locales; la inferencia real requiere el recorrido siguiente.

En curso: los registros definitivos se añadirán al cerrar el recorrido
nativo y CI del commit exacto. Los dobles geométricos prueban autoridad y
transacciones, y no acreditan calidad semántica. Los pesos públicos se preparan
una sola vez por ejecución de CI; sus pruebas reales son obligatorias, no un skip
silencioso. Las suites completas permanecen fuera del Mac del dueño.

macOS rechaza aplicar el Seatbelt del worker dentro del Seatbelt exterior de los
focales locales. Esos focales comprueban protocolo y cierre con procesos sintéticos;
la cualificación real usa el worker productivo en CI, sin retirar su aislamiento.
Los permisos TCC, credenciales y datos personales no forman parte de este bloque.

Este incremento aborda A-24/A-25/A-26. Los motores opcionales y wiki de A-27, la
distribución autocontenida y el cierre global de fase A no se declaran completados.
La fase B no se inicia.
