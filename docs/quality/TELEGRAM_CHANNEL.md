# Telegram en Jarvis

Jarvis recibe texto desde un bot exclusivo mediante `getUpdates`, con conexiones HTTPS salientes. No necesita dominio, webhook ni puerto público. El Mac debe estar despierto, conectado y ejecutando Jarvis. No es un servicio alojado en la nube.

## Configurar

1. En Configuración → Telegram introduce el token de tu bot de BotFather y pulsa «Guardar y verificar».
2. Pulsa «Vincular móvil», abre el enlace en tu cuenta de Telegram y pulsa Iniciar.
3. Vuelve a Configuración, comprueba el nombre y el ID numérico y pulsa «Autorizar esta cuenta».
4. Escribe al bot. `/ayuda`, `/estado`, `/nuevo` y `/cancelar` controlan la conversación.

Admite texto, notas de voz, imágenes y documentos con los límites detallados abajo. El historial vive en una conversación propia, cifrada con el mismo almacén del chat del Mac. Borrar esa conversación desde el Mac requiere iniciar otra con `/nuevo` para seguir usando Telegram.

Las instrucciones, el historial y los resultados solicitados usan el pipeline NVIDIA existente. Telegram transporta instrucciones y respuestas; el usuario acepta este acceso en el Mac al vincular. El acceso continúa con la pantalla bloqueada si el daemon sigue activo; las herramientas mantienen sus requisitos de presencia local, permisos y voz verificada.

Las preguntas como «Cuáles son mis últimos 5 correos sin leer» seleccionan la búsqueda de Gmail antes de ofrecer lectura de mensajes individuales. NVIDIA propone sus filtros y cantidad; el broker sigue exigiendo la aprobación de la consulta concreta. Telegram informa las causas públicas de fallos (NVIDIA, límites, historial o aprobación) sin exponer respuestas internas ni credenciales. No reintenta automáticamente herramientas ya aprobadas.

La copia de los esquemas enviada a NVIDIA representa los argumentos opcionales con valor predeterminado nulo mediante omisión, evitando un bloqueo observado del decodificador en consultas de Gmail con historial. El esquema original del broker no cambia; los argumentos obligatorios y las validaciones de permisos permanecen intactos. Una respuesta ordinaria ya contestada, sin resultados ni pasos, permanece en el historial pero no se vuelve a presentar como tarea activa. El respaldo predeterminado del planificador y router es `openai/gpt-oss-20b` a través de NVIDIA, verificado con la consulta y su aprobación pendiente; reemplaza un endpoint que devolvía HTTP 410. Al proponer una herramienta concreta, el primer intento dispone de hasta 12 segundos si hay un respaldo disponible; ambos comparten el límite total existente. Esto reintenta únicamente la inferencia, nunca la ejecución de herramientas. La auditoría guarda el código HTTP público cuando existe, sin cuerpos de respuesta ni credenciales.

## Identidad y credenciales

El token se guarda exclusivamente en el llavero `ai.aegis.telegram-bot`. La configuración privada `telegram.json` guarda el bot, propietario numérico, conversación, habilitación y offset, nunca el token. Estados y errores no incluyen secretos. Los registros HTTP filtran tokens de bot.

La vinculación tiene un código aleatorio en memoria, caduca en cinco minutos y limita intentos. Abrir el enlace registra solo un candidato; no concede acceso. El IPC local autenticado exige confirmación del ID exacto. Solo se acepta el chat privado cuya identidad y chat coinciden con el propietario autorizado. Se ignoran grupos, canales y otros usuarios.

No se modifica un webhook existente: se rechaza ese bot para evitar interrumpir otras integraciones. Al reiniciar se comprueba que la credencial corresponde al mismo bot guardado. Un conflicto con otro receptor se informa en Configuración.

## Acciones y ciclo de vida

La interfaz de aprobación presenta el resumen y los campos normalizados de la acción. Los botones incluyen un nonce opaco, vinculado al trabajo, digest, mensaje, cuenta y caducidad. La aprobación usa `verified_telegram`, nunca la identidad local ni evidencia biométrica. La auditoría registra `telegram-user:<id>`. El broker consume el permiso una sola vez; repetir el botón no repite la acción.

El offset se guarda antes de procesar cada actualización. Un reinicio no vuelve a ejecutar una acción aceptada; recupera su respuesta o informa de la interrupción, y requiere una solicitud explícita para volver a actuar. Se ignoran mensajes con más de cinco minutos. No hay cola automática de acciones. Los mensajes consecutivos se agrupan para completar una solicitud; una tarea en curso debe terminar o cancelarse antes de enviar otra. Las aclaraciones conservan la solicitud y su vencimiento.

Desactivar, desvincular o cambiar bot cancela el trabajo propio e invalida decisiones y entregas de la sesión anterior. La recepción y los trabajos se detienen durante la suspensión de recursos o un fallo de integridad. Los productores se cierran antes de los jobs y del almacén durante el apagado del daemon.

La app compara periódicamente el estado de energía del daemon con una observación actual de macOS. Si una notificación o entrega IPC se perdió, vuelve a sincronizarlo. El Modo de bajo consumo pausa únicamente la escucha automática en segundo plano y la palabra de activación; Telegram, chat, CLI, herramientas y voz manual continúan disponibles y no se interrumpe una consulta en curso. Una temperatura insegura o desconocida mantiene la suspensión térmica. El estado conserva el indicador real de bajo consumo aunque el servicio permanezca activo.

## Validación

`tests/test_telegram.py` prueba consentimiento local, identidad privada exacta, intentos y vencimiento de vínculo, secretos, offsets durables, contexto, aclaraciones, entrega de resultados, callbacks falsificados/repetidos/caducados, suspensión y cancelación de sesiones. Prueba también errores HTTP acotados, HTML escapado, segmentos UTF-16 y ausencia de redes mientras esté desactivado. `tests/test_jobs.py` valida una aprobación remota contra el broker y auditoría reales.

La comprobación real requiere el token del propietario, vincular su móvil y una conversación de prueba. No se deben usar tokens reales en pruebas ni compartirlos en el chat de Codex.

Documentación oficial: https://core.telegram.org/bots/api y https://core.telegram.org/bots/features#botfather.

## Continuar conversaciones del Mac

Telegram recupera automáticamente el contexto pertinente de todos los chats activos del propietario: fragmentos históricos, archivos de texto adjuntos, resúmenes y objetivos con sus fechas. No hace falta usar `/chats` ni seleccionar un hilo para recordar algo hablado en el Mac. Se mantiene el historial propio de Telegram y la memoria compartida del propietario. Los chats en la papelera y los de otro namespace quedan excluidos. `/nuevo` limpia el hilo activo sin perder ese acceso a los recuerdos. La búsqueda utiliza términos normalizados, tiene un presupuesto de tres segundos y entrega hasta cuatro fuentes en 8 KiB; informa al modelo cuando la búsqueda o los fragmentos son parciales. Un historial previo nunca concede permisos ni demuestra una ejecución actual.

`/chats` muestra hasta diez conversaciones activas con su comando `/continuar UUID`. El propietario vinculado puede elegir una y su siguiente mensaje usa ese historial, resumen y progreso. En el Mac, el menú contextual de un chat ofrece «Continuar en Telegram». La selección no envía contenido ni ejecuta acciones; requiere el bot habilitado y el propietario ya vinculado y no reemplaza una tarea en curso. `/nuevo` vuelve a una conversación independiente. Historial y memoria siguen sujetos a las comprobaciones de identidad del canal y las acciones siguen solicitando su aprobación exacta. Véase ADR-0230 para persistencia, recuperación y límites de los resúmenes.

## Conversación y contenido desde el móvil

La integración admite texto, notas de voz OGG/Opus de hasta dos minutos, imágenes
JPEG/PNG/WebP y archivos de texto o PDF con texto. Descarga únicamente archivos
identificados por Telegram, hasta 10 MB (8 MB para voz), sin seguir redirecciones ni
rutas externas. Los PDFs escaneados requieren una imagen de la página. Cada solicitud
admite cuatro documentos, una imagen y 64 KiB de texto extraído, con 32 KiB por documento.
La imagen se normaliza al contrato de visión existente de 32 KiB y pierde sus metadatos.

La extracción de PDF y normalización de imágenes se ejecutan en procesos desechables:
12 segundos de límite, CPU acotada y vigilancia de memoria. Los archivos originales
no se guardan en el historial. Las notas se transcriben en NVIDIA Canary por gRPC TLS
(`grpc.nvcf.nvidia.com:443`, función `b0e8b4a5-217c-40b7-9b96-17d84e666317`, español
`es-ES`), con el mismo llavero NVIDIA. La transcripción se muestra al propietario y
entra como texto del canal vinculado: nunca afirma una identidad biométrica de voz.
El botón «Escuchar» utiliza la voz NVIDIA actual; el codificador de audio de macOS
la convierte a M4A temporal, que se elimina después del envío o cancelación. No
requiere FFmpeg, un modelo local ni una voz nueva.

La lectura omite las marcas Markdown y los enlaces de seguimiento, conservando sus
etiquetas. Presenta «Preparando el audio con NVIDIA…» y «Detener audio», con un control
ligado al propietario y mensaje exactos. Al terminar informa si el audio está listo,
se canceló o falló la síntesis, la conversión o el envío. Los registros guardan solo
la etapa, tipo de error y código HTTP público. Una referencia recortada o un texto
de más de 2.000 caracteres requiere resumir antes de escucharlo; no se lee un fragmento
como si fuera el mensaje completo.

Los mensajes consecutivos se agrupan durante 1,2 segundos de quietud (máximo cuatro).
Un texto enviado mientras se prepara un adjunto puede completar su instrucción.
Una corrección explícita mientras espera aprobación cancela la propuesta antigua y
crea una solicitud nueva; el botón anterior no puede aprobar la propuesta nueva.
Una acción que ya está en ejecución no se reemplaza automáticamente.

Pedir «Explica cómo organizar…», una definición, un resumen o un ejemplo no activa
investigación de capacidades por mencionar un verbo operativo. Los verbos dentro
de citas tampoco la activan. La explicación sigue el diálogo NVIDIA ordinario;
las órdenes reales de aprender una integración conservan la investigación pública
acotada y los controles existentes.

El chat escrito no recibe el límite de frases ni las instrucciones de cadencia de voz.
El historial mantiene los roles y, antes de la petición actual, un recordatorio de sistema
evita tratar respuestas anteriores como ejemplos correctos que deban imitarse. No
elimina los datos que permiten resolver seguimientos.
La instrucción de diálogo NVIDIA distingue el objetivo de las condiciones de salida:
cantidad, extensión, idioma y formato pertenecen a la petición actual. Una respuesta
sin introducción no debe añadir una descripción de lo que va a entregar ni convertir
el formato solicitado en el tema de la explicación. La prueba optativa
`test_nvidia_explanation_respects_topic_and_output_constraints` verifica este contrato
con la API real y contexto sintético, incluido un turno anterior incorrecto.

Las respuestas ordinarias de texto con historial o restricciones explícitas se mantienen
en un búfer antes de publicarlas. Primero NVIDIA identifica el objetivo actual y si
necesita contexto, en una llamada que no recibe borradores, historial ni resúmenes.
Si es una petición completa e independiente, genera una respuesta nueva sin esas
respuestas anteriores; el historial almacenado se conserva. Los seguimientos y las
peticiones que necesitan datos personales, recuerdos o estado actual mantienen todo
el contexto autorizado como datos falibles. Una revisión cerrada comprueba y puede
reescribir la respuesta. Usa los modelos de síntesis configurados,
prefiriendo uno distinto del que produjo el borrador cuando está disponible, sin cambiar
las preferencias del usuario. Los límites explícitos de palabras, párrafo, introducción
y pregunta final se comprueban antes de publicar. Hay como máximo dos intentos de
revisión después de extraer el objetivo, dentro de un total de 40 segundos. La extracción
dispone de hasta 12 segundos y la primera revisión de hasta 20; cada llamada comparte
ese presupuesto con el respaldo remoto configurado. La síntesis con GPT-OSS usa el
[esfuerzo bajo documentado por NVIDIA](https://docs.api.nvidia.com/nim/reference/openai-gpt-oss-20b-infer).
Los roles de planificación conservan su comportamiento. Un fallo
no publica ni persiste el borrador rechazado. Esto añade dos llamadas a NVIDIA en
esos turnos, y puede añadir otra si la revisión no
cumple el contrato. Voz, visión, ingeniería, propuestas de herramientas y juicios
estructurados conservan sus rutas. La revisión no ofrece ni ejecuta herramientas y no
puede aprobar acciones. La auditoría guarda únicamente estado, modelo y motivo acotado.

Comprobación con NVIDIA del 5 de octubre de 2026: pasaron la reparación de una respuesta
que confundía organizar tareas con redactar un párrafo, la explicación con historial de
frutas, la misma petición con una respuesta anterior equivocada y el seguimiento «la
segunda» entre dos opciones. Las pruebas usan contexto sintético y no ejecutan herramientas.
El propietario confirmó la entrega de audio corto y del párrafo largo en Telegram.

Las referencias explícitas hacia atrás («el primer paso», «la segunda», «tu respuesta»)
conservan contexto aunque el extractor remoto las clasifique erróneamente como
independientes. El último intercambio se identifica por separado de los resúmenes
históricos para resolver el referente más reciente; una cita explícita puede seleccionar
otro turno. Esta salvaguarda no ejecuta acciones ni concede permisos. La regresión
reproduce un resumen antiguo que indica un paso distinto del último párrafo: el
seguimiento debe explicar el paso del párrafo nuevo. La clasificación errónea y la
presencia del párrafo correcto en el historial instalado se verificaron por separado,
sin imprimir historial privado ni credenciales.
La comprobación remota pasó los seis escenarios de reparación, formato, cambio de
tema y seguimiento, incluido «el primer paso» con resumen antiguo contradictorio.

La primera revisión del borrador por sí sola pasó pruebas simples, pero falló al
repetir la petición varias veces en el historial real de Telegram. La extracción
independiente del objetivo y la selección semántica de contexto cubren esa variante:
la regresión incluye varias respuestas equivocadas y un resumen que repite su error.

Al usar «Responder» de Telegram se busca el mensaje en referencias propias cifradas,
ligadas al bot, propietario y conversación. No se confía en el texto de la cita ni
se toma otra lista reciente como sustituto. Las referencias duran hasta 48 horas,
con máximo 16 mensajes; una selección ordinal de una lista requiere evidencia reciente.
Los chats eliminados o en papelera quedan excluidos. Estas referencias son datos
históricos: no otorgan permisos, no prueban el estado actual ni reactivan acciones.

Un mensaje de progreso presenta el estado real y botones «Cancelar» y «Ver estado».
La preparación de audio/archivos también puede cancelarse. Las respuestas permiten
«Escuchar» y muestran «Resumir» a partir de 160 caracteres; las listas verificadas
ofrecen «Leer» para los primeros cinco correos y una lectura verificada ofrece
«Preparar borrador», sin volver a ofrecer leer ese mismo correo. Una aclaración sobre
una lista de correos puede ofrecer botones de selección y cancelar la aclaración.
Cada botón se liga al mensaje, propietario, conversación, nonce y vigencia. Usarlos
crea una solicitud ordinaria: el broker conserva todas las aprobaciones y verificaciones.

Cada resultado verificado conserva el UUID de la solicitud que lo obtuvo. Los botones
de una respuesta final utilizan exclusivamente la evidencia de esa solicitud: una
respuesta ordinaria no hereda correos de búsquedas anteriores. Una lista, incluso de
un solo correo, no ofrece preparar un borrador antes de leer su contenido. Las
referencias de versiones anteriores conservan el texto para citas pero pierden
las selecciones sin procedencia comprobable; requieren actualizar la consulta.

## Entrega durable y reinicios

`telegram-transport.json` es un sobre AEAD cifrado con la clave de memoria existente,
con escritura atómica, fsync y permisos 0600. Guarda referencias acotadas, hasta tres
respuestas pendientes y un marcador de solicitud interrumpida; nunca credenciales,
audios originales ni aprobaciones. Se guarda el resultado antes de enviarlo y se
confirma cada segmento por separado. Un fallo de presentación o entrega no cancela
una operación que ya terminó. Se reintenta solo la entrega, con una pausa entre intentos.

Telegram no ofrece una clave de idempotencia para `sendMessage`. Si hubo timeout o
reinicio después del envío pero antes de registrar su acuse, la respuesta recuperada
avisa que puede haberse entregado antes. Esto puede duplicar un segmento de texto;
nunca repite la herramienta. Cambiar de propietario/bot, desactivar el canal o borrar
el chat impide entregar resultados de la sesión anterior.

Los nuevos intercambios guardan su UUID de solicitud dentro del historial cifrado,
compatible con turnos anteriores sin ese campo. Tras un reinicio se recupera únicamente
la respuesta del UUID exacto o la evidencia verificada del mismo journal de operación.
Si la ejecución quedó indeterminada, se informa expresamente. No se reenvía ninguna
herramienta ni se restaura una aprobación. El historial y los resultados de otras
solicitudes no se atribuyen a la operación interrumpida.

Validación adicional: `tests/test_telegram_experience.py` cubre contexto citado,
correcciones, selección, procedencia por solicitud, migración de referencias, límites,
procesos de adjuntos, entrega pendiente, reinicios, identidad, borrado, cancelación
y errores de audio por etapa; `tests/test_nvidia_telegram_asr.py` verifica el
contrato de transcripción NVIDIA. La prueba móvil real sigue requiriendo interacción
del propietario; no se envían mensajes al bot desde estas pruebas.

Referencias oficiales: [Telegram Bot API](https://core.telegram.org/bots/api),
[NVIDIA Canary](https://build.nvidia.com/nvidia/canary-1b-asr/api) y
[Riva ASR](https://docs.nvidia.com/nim/speech/latest/reference/api-references/asr/protos.html).
