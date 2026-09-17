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
ventana puede mostrar hasta 1.000 mensajes cargados de una conversación. Empezar otra
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

## Historial y archivos

El panel **Historial** permite buscar conversaciones por título, abrirlas y continuar
con el mismo contexto. **Cargar mensajes anteriores** recupera páginas anteriores del
chat; **Cargar más conversaciones** amplía la lista. El botón de la barra de la ventana
permite ocultar el historial. Las conversaciones anteriores a esta interfaz también
aparecen; sus títulos genéricos se presentan usando la primera pregunta disponible.

Para eliminar un chat, selecciónalo y pulsa el icono de papelera del historial,
o usa **Mover a la papelera** en su menú contextual. Se retira del historial activo
y puedes recuperarlo desde **Papelera → Restaurar**. No se borran los archivos originales.

El clip del editor abre el selector de archivos. También puedes arrastrarlos al editor
y quitar cada adjunto antes de enviar. Se admiten texto UTF-8/UTF-16, Markdown, código,
CSV, JSON, PDF con texto seleccionable e imágenes. Se preparan localmente y NVIDIA
recibe el contenido únicamente al enviar el mensaje. Si no escribes una pregunta,
Jarvis analiza los adjuntos. No se leen rutas sugeridas por el modelo.

Se admiten cuatro archivos por mensaje, incluida una imagen: hasta 10 MB por archivo
de origen, 32 KB de texto por documento y 64 KB de texto total. Un PDF escaneado o
protegido debe convertirse en texto o adjuntarse como imagen de la página. Un archivo
que supera el límite se rechaza con un aviso; no se recorta silenciosamente.

El texto extraído de los documentos queda cifrado en la conversación y puede usarse
al retomarla, incluso después de reiniciar. El contexto de archivos conserva hasta
cuatro nombres recientes dentro del presupuesto de 64 KB; una copia nueva del mismo
nombre sustituye a la anterior para el análisis. Las imágenes se optimizan y se usan
solo en el turno actual; el historial conserva su nombre. Los archivos no conceden
permisos ni pueden sustituir las instrucciones del usuario.

## Formato de las respuestas

El chat interpreta el Markdown de Jarvis con el parser nativo de macOS: negritas,
cursivas, enlaces, títulos, listas, citas, tablas y bloques de código. Las listas
mantienen su numeración y alinean el texto de continuación; el código conserva
su contenido literal y permite desplazamiento horizontal. Los mensajes del usuario
se muestran literalmente. Copiar un mensaje conserva el texto original de la respuesta.
