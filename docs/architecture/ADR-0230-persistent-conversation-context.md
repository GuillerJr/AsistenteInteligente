# ADR-0230: Contexto persistente de LangGraph con NVIDIA

## Decisión

Jarvis mantiene el grafo de ejecución existente y añade un grafo de contexto persistente. `ConversationContextManager` compila un `StateGraph` con `ConversationCheckpointSaver`: los checkpoints y escrituras pendientes se guardan como JSON en la fila AEAD de la conversación, ligada a namespace e identidad. No usa pickle, clases ejecutables, credenciales ni callbacks. Se conservan tres checkpoints por chat; el documento tiene un límite de 128 KiB. La escritura vuelve a leer la fila bajo transacción para conservar modificaciones concurrentes del título, historial y progreso.

El grafo de contexto resume los mensajes que quedan fuera de la ventana real de JSON/UTF-8 enviada al modelo, no solamente los que exceden el contador de mensajes. Lee lotes contiguos de hasta doce turnos y conserva resumen previo, cobertura y digest de las fuentes. NVIDIA genera un resumen acotado; el presupuesto agregado de preparación es diez segundos y cuatro lotes. Un error mantiene el resumen anterior y declara cobertura parcial; incluye fragmentos identificados de las fuentes pendientes. El siguiente turno puede volver a intentarlo. Las fuentes completas siguen disponibles en el historial cifrado. Los resúmenes son referencias inferidas de ese chat y no hechos globales, pruebas de ejecución ni permisos.

Solo el nodo de resumen puede reanudarse con `ainvoke(None)`. No se serializa ni reanuda el grafo que ejecuta herramientas: sus autorizaciones caducan y el broker vuelve a evaluar cada solicitud. El journal de interrupción guarda intención y fases `awaiting_confirmation`, `executing`, `observed`, `completed`, sin argumentos ejecutables ni concesiones. Antes de ejecutar una herramienta aprobada se escribe `executing`; su resultado verificado se conserva antes de la síntesis remota. Un seguimiento corto de una operación interrumpida no la repite; si falta resultado informa que podría haberse ejecutado. No se promete exactamente una ejecución frente a un fallo del proveedor: una respuesta perdida exige comprobar el resultado externo antes de repetir.

## Memoria e identidad

La recuperación de recuerdos y perfil ya no depende de un proveedor de inferencia local cuando la política es NVIDIA. Se utilizan el almacén y las búsquedas locales existentes; solo el razonamiento y los resúmenes usan NVIDIA. Los recuerdos pertinentes, con fuente, confianza y caducidad, se incluyen en planificación y síntesis con sanitización de secretos. El objetivo activo ayuda a buscar recuerdos en seguimientos breves. Las correcciones del usuario prevalecen. La voz no reduce el contexto con presupuestos de prefill de AFM cuando se usa NVIDIA.

El perfil pertenece al namespace del propietario y se comparte entre sus canales autorizados. Los historiales, resúmenes, fuentes y operaciones permanecen separados por UUID. Una voz sin identidad de propietario verificada no recibe perfil, historial, resumen ni journal. El estado actual de energía se obtiene del controlador sincronizado con macOS, con marca de observación; un estado desconocido se presenta como desconocido y nunca se incorpora como preferencia permanente.

## Continuidad

La voz usa el chat seleccionado mediante el mecanismo existente y sus comprobaciones biométricas. El propietario vinculado de Telegram puede listar diez chats con `/chats` y elegir uno con `/continuar UUID`. El menú contextual del chat permite «Continuar en Telegram» mediante IPC autenticado, con feedback visible. Seleccionar un chat no envía mensajes, no ejecuta acciones, no reutiliza aprobaciones y no cancela un trabajo en curso. Un chat archivado o eliminado no puede seleccionarse. Un fork conserva su contexto de referencia en una copia independiente; nunca copia el journal de ejecución.

## Validación

`test_conversation_context.py` ejecuta LangGraph real para comprobar reinicios, interrupción y reanudación del resumen, cobertura real, proveedor no disponible, cifrado, retención, aislamiento, forks, eliminación, concurrencia y exclusión de voz no verificada. Comprueba que NVIDIA recibe perfil, resumen y energía actual sin proveedor local. `test_telegram.py` verifica selección por el propietario, rechazo de terceros y tareas activas, persistencia del UUID y ausencia de aprobación o envío al seleccionar. Las pruebas reales de NVIDIA usan datos sintéticos y no ejecutan herramientas ni consultan datos de Google.
