# Telegram en Jarvis

Jarvis recibe texto desde un bot exclusivo mediante `getUpdates`, con conexiones HTTPS salientes. No necesita dominio, webhook ni puerto público. El Mac debe estar despierto, conectado y ejecutando Jarvis. No es un servicio alojado en la nube.

## Configurar

1. En Configuración → Telegram introduce el token de tu bot de BotFather y pulsa «Guardar y verificar».
2. Pulsa «Vincular móvil», abre el enlace en tu cuenta de Telegram y pulsa Iniciar.
3. Vuelve a Configuración, comprueba el nombre y el ID numérico y pulsa «Autorizar esta cuenta».
4. Escribe al bot. `/ayuda`, `/estado`, `/nuevo` y `/cancelar` controlan la conversación.

La primera versión admite texto. No descarga mensajes de voz ni adjuntos. El historial vive en una conversación propia, cifrada con el mismo almacén del chat del Mac. Borrar esa conversación desde el Mac requiere iniciar otra con `/nuevo` para seguir usando Telegram.

Las instrucciones, el historial y los resultados solicitados usan el pipeline NVIDIA existente. Telegram transporta instrucciones y respuestas; el usuario acepta este acceso en el Mac al vincular. El acceso continúa con la pantalla bloqueada si el daemon sigue activo; las herramientas mantienen sus requisitos de presencia local, permisos y voz verificada.

Las preguntas como «Cuáles son mis últimos 5 correos sin leer» seleccionan la búsqueda de Gmail antes de ofrecer lectura de mensajes individuales. NVIDIA propone sus filtros y cantidad; el broker sigue exigiendo la aprobación de la consulta concreta. Telegram informa las causas públicas de fallos (NVIDIA, límites, historial o aprobación) sin exponer respuestas internas ni credenciales. No reintenta automáticamente herramientas ya aprobadas.

La copia de los esquemas enviada a NVIDIA representa los argumentos opcionales con valor predeterminado nulo mediante omisión, evitando un bloqueo observado del decodificador en consultas de Gmail con historial. El esquema original del broker no cambia; los argumentos obligatorios y las validaciones de permisos permanecen intactos. Una respuesta ordinaria ya contestada, sin resultados ni pasos, permanece en el historial pero no se vuelve a presentar como tarea activa. El respaldo predeterminado del planificador y router es `openai/gpt-oss-20b` a través de NVIDIA, verificado con la consulta y su aprobación pendiente; reemplaza un endpoint que devolvía HTTP 410. Al proponer una herramienta concreta, el primer intento dispone de hasta 12 segundos si hay un respaldo disponible; ambos comparten el límite total existente. Esto reintenta únicamente la inferencia, nunca la ejecución de herramientas. La auditoría guarda el código HTTP público cuando existe, sin cuerpos de respuesta ni credenciales.

## Identidad y credenciales

El token se guarda exclusivamente en el llavero `ai.aegis.telegram-bot`. La configuración privada `telegram.json` guarda el bot, propietario numérico, conversación, habilitación y offset, nunca el token. Estados y errores no incluyen secretos. Los registros HTTP filtran tokens de bot.

La vinculación tiene un código aleatorio en memoria, caduca en cinco minutos y limita intentos. Abrir el enlace registra solo un candidato; no concede acceso. El IPC local autenticado exige confirmación del ID exacto. Solo se acepta el chat privado cuya identidad y chat coinciden con el propietario autorizado. Se ignoran grupos, canales y otros usuarios.

No se modifica un webhook existente: se rechaza ese bot para evitar interrumpir otras integraciones. Al reiniciar se comprueba que la credencial corresponde al mismo bot guardado. Un conflicto con otro receptor se informa en Configuración.

## Acciones y ciclo de vida

La interfaz de aprobación presenta el resumen y los campos normalizados de la acción. Los botones incluyen un nonce opaco, vinculado al trabajo, digest, mensaje, cuenta y caducidad. La aprobación usa `verified_telegram`, nunca la identidad local ni evidencia biométrica. La auditoría registra `telegram-user:<id>`. El broker consume el permiso una sola vez; repetir el botón no repite la acción.

El offset se guarda antes de procesar cada actualización. Un reinicio no vuelve a ejecutar una acción aceptada; si interrumpió una solicitud hay que reenviarla explícitamente. Se ignoran mensajes con más de cinco minutos. No hay cola automática de acciones. Una solicitud en curso debe terminar o cancelarse antes de enviar otra. Las aclaraciones conservan la solicitud y su vencimiento.

Desactivar, desvincular o cambiar bot cancela el trabajo propio e invalida decisiones y entregas de la sesión anterior. La recepción y los trabajos se detienen durante la suspensión de recursos o un fallo de integridad. Los productores se cierran antes de los jobs y del almacén durante el apagado del daemon.

La app compara periódicamente el estado de energía del daemon con una observación actual de macOS. Si una notificación o entrega IPC se perdió, vuelve a sincronizarlo. El Modo de bajo consumo pausa únicamente la escucha automática en segundo plano y la palabra de activación; Telegram, chat, CLI, herramientas y voz manual continúan disponibles y no se interrumpe una consulta en curso. Una temperatura insegura o desconocida mantiene la suspensión térmica. El estado conserva el indicador real de bajo consumo aunque el servicio permanezca activo.

## Validación

`tests/test_telegram.py` prueba consentimiento local, identidad privada exacta, intentos y vencimiento de vínculo, secretos, offsets durables, contexto, aclaraciones, entrega de resultados, callbacks falsificados/repetidos/caducados, suspensión y cancelación de sesiones. Prueba también errores HTTP acotados, HTML escapado, segmentos UTF-16 y ausencia de redes mientras esté desactivado. `tests/test_jobs.py` valida una aprobación remota contra el broker y auditoría reales.

La comprobación real requiere el token del propietario, vincular su móvil y una conversación de prueba. No se deben usar tokens reales en pruebas ni compartirlos en el chat de Codex.

Documentación oficial: https://core.telegram.org/bots/api y https://core.telegram.org/bots/features#botfather.

## Continuar conversaciones del Mac

`/chats` muestra hasta diez conversaciones activas con su comando `/continuar UUID`. El propietario vinculado puede elegir una y su siguiente mensaje usa ese historial, resumen y progreso. En el Mac, el menú contextual de un chat ofrece «Continuar en Telegram». La selección no envía contenido ni ejecuta acciones; requiere el bot habilitado y el propietario ya vinculado y no reemplaza una tarea en curso. `/nuevo` vuelve a una conversación independiente. Historial y memoria siguen sujetos a las comprobaciones de identidad del canal y las acciones siguen solicitando su aprobación exacta. Véase ADR-0230 para persistencia, recuperación y límites de los resúmenes.
