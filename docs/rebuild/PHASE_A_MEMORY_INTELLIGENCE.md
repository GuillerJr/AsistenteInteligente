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
La búsqueda manual puede mostrar candidatos débiles. La recuperación automática
semántica/combinada exige además coseno bruto mínimo de 0,35 antes de la fusión;
un rango RRF alto no sustituye esa condición. Es una regla conservadora de
abstención, con posibles falsos negativos, no una probabilidad de respuesta
correcta ni un umbral universal del modelo. La búsqueda léxica sigue explícita.
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
Una búsqueda habilitada sin coincidencias aptas entrega una cápsula vacía
explícita; una política desactivada no envía metadatos de memoria. El adaptador
advierte que los candidatos pueden no contestar la pregunta y debe indicar
ausencia de respaldo en vez de sustituir una nota por otra parecida. Esto reduce
asociaciones incorrectas; no convierte un modelo generativo en un verificador.

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

Los últimos focales pasaron 78 casos: 41 de contexto/autoridad y 37 de índice/
entrega, con fuentes intactas y cierre físico confirmado. Sus máximos fueron
5.226.496 y 6.275.072 bytes; plazos de 180 s, presupuesto vigilado de 256 MiB y
volumen APFS limitado físicamente a 1 GiB. Los focales anteriores del supervisor,
paquete y protocolo mantienen su evidencia separada. No se repite la suite
completa en el Mac del dueño.

El recorrido nativo usó la app separada «Jarvis Memoria Prueba», un perfil nuevo,
cuatro notas artificiales y el acceso oficial a ChatGPT completado por el dueño.
Guardado aprobado → índice automático (generación 5), corrección (6) y olvido
(7) quedaron verificados por fuente y recibos. El primer ensayo de ausencia
descubrió una asociación incorrecta con un distractor: coseno 0,19 y RRF 0,99.
No se ocultó como éxito del olvido. Después del ajuste de abstención, un chat
nuevo recibió cero citas y respondió que el dato no constaba.

El control positivo posterior guardó otra vez la nota (generación 8); otro chat
recuperó el marcador sin que estuviera en la pregunta. Tras corregirla (9), otro
chat respondió con el valor nuevo. Al revocar la política (revisión 5), un chat
nuevo recibió estado «disabled» y cero citas aunque la nota seguía guardada.
Los hashes de las cuatro notas originales permanecieron iguales. El cierre
liberó la custodia, retiró el socket y dejó cero temporales del worker.

El panel nativo anterior provocaba un ciclo de restricciones al abrirse. Se
sustituyó por un panel SwiftUI explícito y se ensayaron apertura, cambios de
tamaño y contenido. Las filas AX ahora publican sus valores de forma explícita;
se observó revisión 3→4→5 en el mismo panel. Los intentos fallidos y la distinción
entre código UI y servicio Python externo quedan en el
[registro verificable](PHASE_A_MEMORY_QUALIFICATION.json). El bundle de desarrollo
no constituye una distribución autocontenida ni reemplaza la app del dueño.

La CI de `075b9dd` aprobó Python (2885 casos macOS; 2839 y 46 omisiones
Ubuntu). El lector AX del test encontró un bridge tipado inválido de AppKit;
se retiró ese acceso sin retirar oráculos. La lectura virtual siguió sin publicar
IDs en el fixture. Las filas usan ahora un NSView accesible estable en un overlay
transparente; SwiftUI conserva el valor y el dibujo. El test consulta el Form
productivo y comprueba la identidad del mismo NSView al cambiar política y trabajo.
El puente pasó la compilación acotada y una lectura AX real de revisión 5→6→7
en el mismo panel, seguida de cierre confirmado. La CI del test ajusta además
la nulabilidad distinta de accessibilityIdentifier entre interfaces del SDK.
Falta confirmar su ejecución completa. Los dobles geométricos prueban
autoridad y transacciones, no calidad semántica. Los pesos públicos se preparan
una sola vez por ejecución remota y sus casos CPU reales son obligatorios;
faltar los artefactos provoca fallo, no una omisión silenciosa.

macOS rechaza aplicar el Seatbelt del worker dentro del Seatbelt exterior de los
focales locales. Esos focales comprueban protocolo y cierre con procesos sintéticos;
la cualificación real usa el worker productivo en CI, sin retirar su aislamiento.
Los permisos TCC, credenciales y datos personales no forman parte de este bloque.

Este incremento aborda A-24/A-25/A-26. Los motores opcionales y wiki de A-27, la
distribución autocontenida y el cierre global de fase A no se declaran completados.
La fase B no se inicia.
