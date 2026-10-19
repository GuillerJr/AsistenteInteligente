# Diálogo y consultas con NVIDIA

## Problema

«Puedes ver mis últimos correos pendientes?» podía terminar en una oferta sin consultar nada.
Un «sí» posterior repetía la oferta: las herramientas de Google requerían mencionar literalmente
«Google Workspace», y el seguimiento no recuperaba la selección de herramientas. La ruta híbrida
podía descartar los esquemas durante la primera pasada local. La aprobación ejecutaba una herramienta
pero terminaba en su presentación técnica, sin una síntesis que respondiera a la petición.

## Comportamiento

- El daemon usa `NvidiaBrainClient` para toda inferencia de lenguaje. No inicializa AFM, MLX LLM,
  generación local de embeddings, borradores especulativos ni fallback local.
- Se mantienen los modelos y alternativas NVIDIA configurados por rol. La CLI y su IPC rechazan
  `local_only` e `hybrid`.
- Los mensajes conservan roles e historial reciente; la petición actual queda al final. Los datos
  de referencia no pueden introducir instrucciones de sistema ni autorizar herramientas.
- Las consultas habituales ofrecen solamente el dominio solicitado de Gmail, Calendar o Drive.
  Las menciones informativas y ejemplos entre comillas no activan esa selección natural.
- Un «sí» puede recuperar una petición de lectura del intercambio inmediatamente anterior, en
  la misma conversación y dentro de cinco minutos, cuando la respuesta anterior ofrecía consultarla.
  No recupera escrituras ni permite acceso al historial de una voz sin identidad verificada.
- Una consulta con herramienta obligatoria admite un reintento ante una oferta sin llamada.
  Otra oferta termina como respuesta incompleta; no se publica como trabajo realizado.
- La cuenta se expone mediante un enum de la configuración confiable. Gmail admite
  `include_headers=true` para obtener asunto, remitente y fecha sin alterar los mensajes.
- La ejecución conserva validación de argumentos, cuentas permitidas y aprobación de un solo uso.
  «Sí» es contexto conversacional, nunca un grant del broker.
- Una herramienta Google aprobada y verificada puede resumirse con NVIDIA. El resumidor no tiene
  herramientas y no repite la ejecución. Si falla, conserva la presentación real del resultado.

El propietario autorizó enviar instrucciones, historial reciente y resultados solicitados de Google
a NVIDIA. Credenciales conocidas y campos estructurados de secretos se filtran antes de serializar,
incluyendo resultados MCP con JSON dentro de texto. Este filtro es heurístico. GraphRAG personal y
biometría no forman parte del nuevo contexto remoto. Activación, transcripción y comprobación de
identidad de voz permanecen en el dispositivo; no son modelos de lenguaje del cerebro de Jarvis.

## Disponibilidad

Una respuesta completa y una herramienta propuesta son contratos distintos. Los fragmentos
truncados no autorizan herramientas ni se guardan como respuestas completas. Credenciales rechazadas,
cuotas y fallos remotos conservan errores separados.

El transporte reserva parte del presupuesto total para la alternativa remota si el primario se
atasca antes de devolver contenido. Después de un fragmento visible no cambia de modelo ni mezcla
respuestas. Los modelos Nemotron Nano/Lightning conocidos utilizan `enable_thinking=false` para
diálogo, planificación inmediata y síntesis; respetan opciones explícitas y conservan los valores
predeterminados en razonamiento crítico y seguridad. La decisión sigue la
[receta oficial de NVIDIA para agentes de voz](https://github.com/NVIDIA-AI-Blueprints/nemotron-voice-agent/blob/main/docs/how-to/configure-llm.md).

La alternativa predeterminada de router y planner se actualizó a DeepSeek V4 Flash 0731 después
de observar HTTP 410 en la alternativa Nano. Se respetan asignaciones personalizadas guardadas.
El transporte aplica `thinking=false` a los modelos DeepSeek V4 conocidos en estos roles inmediatos
y síntesis, sin alterar los valores de los roles especializados. NVIDIA publica el
[endpoint y los parámetros de DeepSeek V4 Flash 0731](https://build.nvidia.com/deepseek-ai/deepseek-v4-flash-0731).

## Verificación

`test_nvidia_dialogue.py` cubre el caso de la captura, historial, selección natural, propuestas
obligatorias, fallos NVIDIA, permisos reales, resumen de resultados y exclusión de secretos.
`test_mcp_integrations.py` comprueba la proyección de cuenta y encabezados, la validación tras
confirmación y que no se ofrecen herramientas de un peer detenido.
`test_nvidia_response_integrity.py` comprueba presupuestos, recuperación remota y ausencia de cambio
de modelo después de contenido visible. Las pruebas optativas `test_nvidia_dialogue_live.py` utilizan
la API real y conversaciones sintéticas; nunca ejecutan una herramienta Google.

Estas verificaciones de texto no certifican las pruebas físicas de voz P10/P11.
