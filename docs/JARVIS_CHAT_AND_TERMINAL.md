# Chat y terminal de Jarvis

En el desplegable de la barra superior, **Abrir chat** presenta una ventana normal
y redimensionable. Se trae al frente al solicitarla; después puedes cambiar de
aplicación con normalidad.

El editor admite varias líneas. Pulsa **Enviar** o **⌘Return** para enviar el
mensaje. Las respuestas aparecen progresivamente y pueden copiarse. **Detener**
cancela la tarea real del daemon y conserva el texto recibido. **Nueva
conversación** inicia un contexto independiente cuando no hay una tarea o una
aclaración pendiente.

El contexto utiliza el almacenamiento local de conversaciones de Jarvis. La
ventana muestra hasta 100 mensajes de la sesión de la aplicación. Empezar otra
conversación no borra los registros locales anteriores. El chat utiliza el
proveedor y las herramientas del daemon y conserva las aprobaciones y
aclaraciones existentes. Sus mensajes no cuentan como evidencia física de voz.

La inferencia del chat, la voz y la CLI utiliza únicamente las APIs de NVIDIA,
con los modelos configurados por rol. No se inicia Apple Foundation Models ni
MLX para pensar, redactar o resumir; no hay sustitución local ante errores de red.
La detección de activación y la verificación de identidad de voz permanecen en
el dispositivo para proteger el control de Jarvis.

Con la autorización del propietario, NVIDIA recibe las instrucciones, el
historial reciente y los resultados solicitados de Gmail, Calendar y Drive.
Las credenciales se filtran; GraphRAG personal y biometría quedan fuera de esos
mensajes. Las peticiones habituales de correo, agenda y Drive ofrecen las
herramientas de Google sin exigir la frase «Google Workspace». Una respuesta
«sí» a una oferta reciente de lectura recupera la petición del usuario; no
consume ni sustituye la aprobación real del broker. Gmail puede incluir asunto,
remitente y fecha en la búsqueda. El resumen utiliza el resultado verificado,
sin ejecutar otra consulta ni afirmar que se envió un borrador.

El apartado **Herramientas de desarrollo → Abrir terminal** abre Terminal con la
CLI interactiva de ingeniería incluida en la aplicación instalada. Se conecta
al daemon existente; no inicia otro ni requiere Python o esta carpeta de código.
Puedes consultar `/help`, `/status`, `/new` y `/workspace` dentro de la sesión.
La CLI conserva los límites de acceso al repositorio autorizados por el daemon.

## Formato de las respuestas

El chat interpreta el Markdown de Jarvis con el parser nativo de macOS: negritas,
cursivas, enlaces, títulos, listas, citas, tablas y bloques de código. Las listas
mantienen su numeración y alinean el texto de continuación; el código conserva
su contenido literal y permite desplazamiento horizontal. Los mensajes del usuario
se muestran literalmente. Copiar un mensaje conserva el texto original de la respuesta.
