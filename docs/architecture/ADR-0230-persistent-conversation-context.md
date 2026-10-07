# ADR-0230: Contexto persistente de LangGraph con NVIDIA

## Decisión

Jarvis mantiene el grafo de ejecución existente y añade un grafo de contexto persistente. `ConversationContextManager` compila un `StateGraph` con `ConversationCheckpointSaver`: los checkpoints y escrituras pendientes se guardan como JSON en la fila AEAD de la conversación, ligada a namespace e identidad. No usa pickle, clases ejecutables, credenciales ni callbacks. Se conservan tres checkpoints por chat; el documento tiene un límite de 128 KiB. La escritura vuelve a leer la fila bajo transacción para conservar modificaciones concurrentes del título, historial y progreso.

El grafo de contexto resume los mensajes que quedan fuera de la ventana real de JSON/UTF-8 enviada al modelo, no solamente los que exceden el contador de mensajes. Lee fragmentos contiguos de hasta 2048 bytes del texto completamente redactado; cada lote contiene hasta doce fragmentos y 12288 bytes serializados como JSON. La cobertura v2 conserva resumen previo, secuencia completa, cursor parcial por byte y digest de las fuentes. Solo avanza la secuencia cubierta al terminar todos los bytes del turno; el contenido intermedio nunca se reemplaza por prefijo y sufijo. Los checkpoints anteriores se reconstruyen desde el historial autenticado, conservando chats y adjuntos. NVIDIA genera un resumen acotado; el presupuesto agregado de preparación es diez segundos y cuatro invocaciones, incluidas las reanudaciones. Un error mantiene el resumen anterior y declara cobertura parcial; incluye fragmentos identificados de las fuentes pendientes. El siguiente turno puede volver a intentarlo. Las fuentes completas siguen disponibles en el historial cifrado. Los resúmenes son referencias inferidas de ese chat y no hechos globales, pruebas de ejecución ni permisos.

La misma llamada de resumen propone hasta cuatro citas de decisiones, cantidades, restricciones,
pendientes o correcciones. Se validan contra los fragmentos originales contiguos; las secuencias,
roles, digest y offsets UTF-8 se derivan localmente. Se conserva la frase con negaciones y
atribución, hasta 256 bytes; una cita ambigua, inventada o incompleta se descarta. La clasificación
es inferida; `quote_verified` solo confirma correspondencia textual, nunca verdad externa. El
ledger cifrado v1 retiene hasta 24 citas y 8 KiB con diversidad por categoría y fuente, y selecciona
citas completas pertinentes en 2 KiB para el modelo. No vuelve a comprimir ni enviar el ledger
anterior a NVIDIA. La salida estructurada completa tiene un presupuesto de 4 KiB. Un checkpoint
v2 previo sin ledger permanece válido, marca evidencia parcial y no provoca una reconstrucción
remota masiva. Leer todos los bytes no garantiza conservar todos los hechos: los originales siguen
siendo la fuente de recuperación cuando la narrativa o el ledger no bastan.

Solo el nodo de resumen puede reanudarse con `ainvoke(None)`. No se serializa ni reanuda el grafo que ejecuta herramientas: sus autorizaciones caducan y el broker vuelve a evaluar cada solicitud. El journal de interrupción guarda intención y fases `awaiting_confirmation`, `executing`, `observed`, `completed`, sin argumentos ejecutables ni concesiones. Antes de ejecutar una herramienta aprobada se escribe `executing`; su resultado verificado se conserva antes de la síntesis remota. Un seguimiento corto de una operación interrumpida no la repite; si falta resultado informa que podría haberse ejecutado. No se promete exactamente una ejecución frente a un fallo del proveedor: una respuesta perdida exige comprobar el resultado externo antes de repetir.

## Memoria e identidad

La recuperación de recuerdos y perfil ya no depende de un proveedor de inferencia local cuando la política es NVIDIA. Se utilizan el almacén y las búsquedas locales existentes; solo el razonamiento y los resúmenes usan NVIDIA. Los recuerdos pertinentes, con fuente, confianza y caducidad, se incluyen en planificación y síntesis con sanitización de secretos. El objetivo activo ayuda a buscar recuerdos en seguimientos breves. Las correcciones del usuario prevalecen. La voz no reduce el contexto con presupuestos de prefill de AFM cuando se usa NVIDIA.

El perfil pertenece al namespace del propietario y se comparte entre sus canales autorizados. El aprendizaje determinista solo consolida afirmaciones propias explícitas: citas, código, terceros, ejemplos, hipótesis y preguntas permanecen en conversación sin convertirse en hechos del perfil. Conserva las preferencias negativas expresas y los comandos directos legítimos; las instrucciones citadas no restablecen ni borran memorias. Los datos anteriores sin procedencia suficiente no se depuran automáticamente. Los historiales, resúmenes, fuentes y operaciones permanecen separados por UUID. Una voz sin identidad de propietario verificada no recibe perfil, historial, resumen ni journal. El estado actual de energía se obtiene del controlador sincronizado con macOS, con marca de observación; un estado desconocido se presenta como desconocido y nunca se incorpora como preferencia permanente.

## Continuidad

Telegram recupera automáticamente referencias pertinentes de todos los chats activos del namespace del propietario, incluso conversaciones fuera de la página reciente y mensajes fuera de su ventana. No requiere seleccionar un chat. La búsqueda lee y autentica el historial cifrado en una transacción de lectura; no crea índices en texto plano ni copias que sobrevivan a eliminar un chat. Incluye fragmentos de mensajes y archivos de texto seleccionados, fechas, roles, digest, resumen inferido cuando existe y objetivo histórico. Recupera también mensajes originales del chat actual fuera de la ventana reciente; Chat, CLI y voz verificada usan esa recuperación exclusivamente dentro de su UUID. Excluye otros namespaces y la papelera. La memoria y perfil del propietario siguen disponibles mediante el recuperador existente.

La ventana reciente se compara con el texto completo de cada turno antes de omitirlo de la
búsqueda. Solo se excluyen turnos plenamente visibles: el centro recortado de un mensaje reciente
largo permanece recuperable desde el original, aunque ese turno aún no haya pasado al resumen.

Cada búsqueda local tiene un presupuesto de tres segundos; declara `search_complete=false` cuando
no termina. Reparte un máximo de 8 KiB entre hasta ocho fuentes pertinentes que quepan; distingue
el recorrido completo de las fuentes omitidas mediante `retrieval_truncated`. La consulta original
domina el ranking, con mayor peso para entidades y términos raros. Las reformulaciones se evalúan
independientemente y se favorecen fuentes complementarias, cifras y correcciones antiguas, incluso
fuera de los últimos cuatro turnos. Puede combinar pasajes distantes con separador explícito; las
citas completas se descartan antes que cortar su importe o procedencia.

Cuando una pregunta histórica o personal tiene cobertura léxica insuficiente, el cerebro NVIDIA
existente propone hasta tres búsquedas breves en una llamada de hasta cuatro segundos. Solo recibe
la consulta y, si hay pronombres que lo requieren, dos mensajes recientes del usuario redactados.
Las propuestas son vocabulario de búsqueda, nunca hechos ni permisos; las entidades explícitas se
preservan. Se vuelve a consultar los originales locales. Un error, cuota, timeout o JSON inválido
conserva la búsqueda local; la cancelación externa se propaga. Un caché acotado de cinco minutos
evita reformular repetidamente la misma consulta y tema, sin almacenar resultados históricos ni
sobrevivir al proceso. No añade una llamada a saludos, confirmaciones, solicitudes actuales de
correo/agenda/energía ni consultas ya bien cubiertas. Esta ruta mejora paráfrasis con expansión
semántica, pero no activa embeddings ni garantiza recuperar cualquier formulación.

El plazo del proveedor para las llamadas estructuradas coincide con el plazo exterior del paso:
el modelo principal dispone como máximo de tres segundos y de la mitad del presupuesto. Así queda
tiempo para el modelo alternativo ya configurado, en vez de que una cancelación exterior interrumpa
el principal antes de llegar al respaldo. No modifica las preferencias de modelos del usuario.

El guard de respuesta conserva citas y extractos pertinentes aunque la extracción de intención
clasifique erróneamente la pregunta como independiente. Las fuentes recuperadas se ligan al hash
de la consulta redactada; esa coincidencia evita perder una paráfrasis sin solape léxico. El hash
solo liga la selección a esa petición, no acredita hechos ni permisos. Una fuente sin esa ligadura
requiere coincidencia de entidad o términos sustantivos. El guard no reintroduce narrativa inferida,
objetivos históricos ni respuestas antiguas al regenerar una respuesta independiente; conserva
roles, procedencia y banderas de parcialidad, también para documentos de otros chats.

Una pregunta por conversaciones recientes permite una vista general. Los seguimientos breves con
tema usan los últimos mensajes del propio hilo como pista secundaria; un «sí» o «continúa» no
selecciona ni activa objetivos ajenos. NVIDIA compara fechas y correcciones, conserva el objetivo
actual y pide precisar el proyecto si hay ambigüedad. Los resultados históricos no prueban el estado
actual de Gmail, energía o herramientas. Nunca se exportan checkpoints ejecutables, journals ni
aprobaciones entre chats. `/nuevo` reinicia el hilo, pero conserva acceso a la memoria compartida.
La selección explícita continúa disponible para retomar exactamente un hilo.

La voz usa el chat seleccionado mediante el mecanismo existente y sus comprobaciones biométricas. El propietario vinculado de Telegram puede listar diez chats con `/chats` y elegir uno con `/continuar UUID`. El menú contextual del chat permite «Continuar en Telegram» mediante IPC autenticado, con feedback visible. Seleccionar un chat no envía mensajes, no ejecuta acciones, no reutiliza aprobaciones y no cancela un trabajo en curso. Un chat archivado o eliminado no puede seleccionarse. Un fork conserva su contexto de referencia en una copia independiente; nunca copia el journal de ejecución.

## Validación

`test_conversation_context.py` ejecuta LangGraph real para comprobar reinicios, interrupción y reanudación del resumen, cobertura real, proveedor no disponible, cifrado, retención, aislamiento, forks, eliminación, concurrencia y exclusión de voz no verificada. Comprueba que NVIDIA recibe perfil, resumen y energía actual sin proveedor local. `test_telegram.py` verifica selección por el propietario, rechazo de terceros y tareas activas, persistencia del UUID y ausencia de aprobación o envío al seleccionar. Las pruebas reales de NVIDIA usan datos sintéticos y no ejecutan herramientas ni consultan datos de Google.

`test_shared_conversation_context.py` cubre recuperación fuera de las ventanas de historial/listado, correcciones, reinicio, adjuntos largos, límite de bytes, aislamiento, papelera, eliminación, autenticación AEAD y cobertura parcial. Ejecuta el pipeline real para verificar inyección automática hacia NVIDIA sin cambiar de UUID ni reutilizar permisos. La prueba opcional de NVIDIA verifica que Telegram responda a una corrección guardada en dos chats del Mac, sin cambiar al chat fuente ni ejecutar herramientas.

`test_retrieval_planning.py` y `test_memory_context_challenges.py` verifican paráfrasis sin términos
comunes, fallback y cancelación, más de cien chats y mil turnos, evidencia contradictoria por entidad,
fuentes complementarias, correcciones antiguas, adjuntos largos, forks y límites reales de bytes.
Las aserciones comprueban datos literales y procedencia; no usan una autoevaluación del modelo.
La prueba optativa de NVIDIA comprueba reformulación y respuesta final sobre conversaciones
sintéticas, sin herramientas externas.
