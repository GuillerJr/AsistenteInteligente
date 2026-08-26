# ADR-0114: respuesta determinista del último correo

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. Una gramática exacta convierte «¿Cuál es mi último correo?», «Dime mi último correo» y
   equivalentes cerrados en `mail_list_recent` con `limit=1` y `unread_only=false`.
2. El script local añade `local_date_received`: un ISO 8601 con el offset del sistema aplicable a la
   fecha concreta del mensaje. La fecha UTC original permanece disponible en el contrato existente.
3. Jarvis valida que remitente y asunto sean imprimibles, normalizados y de hasta 500 caracteres, y
   que la fecha local incluya zona horaria.
4. La respuesta directa presenta remitente, asunto y fecha cuando existen. No solicita ni presenta
   el cuerpo del mensaje.
5. Un inbox vacío, un fallo desconocido o una estructura inválida producen respuestas locales
   específicas. Ninguno invoca planner, Apple Foundation Models ni NVIDIA.
6. Consultas compuestas o que pidan resumen, contenido o múltiples mensajes conservan el cerebro
   normal y sus políticas.
7. No se añade herramienta, permiso, proceso, dependencia, persistencia ni consulta periódica.

## Motivo

Convertir un único registro estructurado de Mail en una frase no necesita inferencia. La ruta directa
reduce latencia y evita compartir metadatos privados con cualquier modelo.

## Límites

- «Último» conserva el orden de mensajes recientes entregado por Apple Mail al contrato existente.
- La fecha se presenta como `DD/MM/AAAA` para no depender del locale del proceso.
- Mail solo se consulta después de una petición explícita y macOS mantiene el control TCC.
- Listar o resumir varios correos conserva el flujo acotado de ADR-0092.
- Enviar correo conserva planificación, política y confirmación de un solo uso.
