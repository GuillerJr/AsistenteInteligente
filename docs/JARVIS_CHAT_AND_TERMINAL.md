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

El apartado **Herramientas de desarrollo → Abrir terminal** abre Terminal con la
CLI interactiva de ingeniería incluida en la aplicación instalada. Se conecta
al daemon existente; no inicia otro ni requiere Python o esta carpeta de código.
Puedes consultar `/help`, `/status`, `/new` y `/workspace` dentro de la sesión.
La CLI conserva los límites de acceso al repositorio autorizados por el daemon.
