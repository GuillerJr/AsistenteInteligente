# ADR-0111: estado privado de correo no leído

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. Una gramática exacta convierte «¿Tengo correos no leídos?», «¿Tengo algún correo sin leer?» y
   equivalentes cerrados en `mail_list_recent` con `unread_only=true` y `limit=1`.
2. El broker, el ejecutor de solo lectura, TCC y la auditoría existentes permanecen obligatorios.
3. Un resultado produce únicamente «Tienes al menos un correo no leído»; cero resultados produce
   «No tienes correos no leídos». Remitente y asunto no se interpolan en la respuesta.
4. Esta ruta no invoca planner, Apple Foundation Models ni NVIDIA. Un fallo desconocido, cantidad
   imposible o estructura inválida también termina en una respuesta determinista local.
5. Consultas de cantidad, órdenes compuestas o frases ambiguas conservan el cerebro normal.
6. No se añade herramienta, permiso, proceso, dependencia, persistencia ni consulta periódica.

## Motivo

Comprobar existencia es una operación binaria que no necesita razonamiento ni red. Reutilizar la
lectura acotada existente reduce latencia y evita exponer metadatos privados a cualquier modelo.

## Límites

- La respuesta afirma existencia, no cuenta mensajes ni identifica remitentes.
- Mail solo se consulta después de una petición explícita; no es un monitor de inbox.
- macOS conserva la decisión TCC sobre Mail.
- Leer una lista o resumir correos continúa usando el flujo acotado de ADR-0092.
- Enviar correo conserva planificación, política y confirmación de un solo uso.
