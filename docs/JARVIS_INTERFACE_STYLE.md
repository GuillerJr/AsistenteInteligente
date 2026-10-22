# Interfaces de Jarvis

La interfaz usa superficies neutras en claro y oscuro, tipografía sans del
sistema y acento arcilla para conservar la identidad de Jarvis. El chat prioriza
la conversación y el editor; la navegación y los paneles de contexto utilizan
un nivel visual más discreto. Los colores adaptativos viven en
`JarvisTheme.swift`. `JarvisInterfaceStyle.swift` reúne las cabeceras de sección,
las superficies interiores, los botones y la disposición de interruptores.

## Paleta y tipografía

| Uso | Claro | Oscuro |
| --- | --- | --- |
| Lienzo | `#FFFFFF` | `#212121` |
| Barra lateral | `#F7F7F7` | `#181818` |
| Superficie elevada y editor | `#F7F7F7` | `#303030` |
| Texto principal | `#181818` | `#ECECEC` |
| Texto secundario | `#5D5D5D` | `#B8B8B8` |
| Mensaje del usuario | `#F1F1F1` | `#353535` |
| Acento arcilla | `#C4643F` | `#D9805C` |

Los estados de selección y paso del puntero usan fondos neutros de distinta
intensidad. El acento arcilla identifica Jarvis, Dot y el modo de plan; no debe
convertirse en el fondo de todas las filas seleccionadas. Advertencia, error y
éxito conservan sus colores semánticos y se acompañan de texto o icono.

La fuente base es la del sistema, también para las respuestas y el editor.
Los títulos se distinguen por peso y jerarquía; los metadatos usan un tamaño
menor y color secundario. El texto monoespaciado se reserva para contenido que
lo necesita, como código, y no para la voz visual general de Jarvis.

## Convenciones

- Las ventanas con título conservan los bordes y esquinas de macOS. Las tarjetas
  interiores usan radios continuos de 12 puntos y el editor de 20 puntos; no
  añaden un segundo marco a la ventana. El HUD conserva su máscara de panel
  independiente.
- Las cabeceras muestran un icono, un título y una explicación que puede ocupar
  varias líneas. Los textos secundarios importantes tienen mayor contraste.
- Los botones muestran estados de pulsación, cursor y disponibilidad. Los
  controles de icono incluyen ayuda y nombre accesible; copiar y quitar adjuntos
  tienen un área de interacción mayor que el símbolo.
- Los interruptores de configuración mantienen la etiqueta a la izquierda y
  el control a la derecha, con el estilo de interruptor de macOS.
- Configuración, voz y decisiones pueden redimensionarse. El contenido central
  se desplaza y las acciones permanecen accesibles en el pie de la ventana.
  Configuración utiliza una ventana auxiliar propia porque el contenedor nativo
  de preferencias mantiene el tamaño fijo. El menú de la app, ⌘, y el icono del
  menubar abren esa misma ventana.
- En chat, la cabecera muestra título y estado, y concentra navegador, contexto
  y opciones adicionales. «Nueva conversación» conserva un acceso de icono
  cuando la barra lateral está oculta. El historial distingue la ausencia de
  chats, la carga, los errores y una búsqueda vacía.
- Reducir transparencia y aumentar contraste siguen las preferencias de macOS.
  La presencia junto al notch también refuerza texto y contorno con alto contraste.
- Jarvis aparece en el Dock y en ⌘Tab mientras Chat, Configuración o un panel de
  voz estén abiertos. Minimizar una ventana u ocultar la app conserva ese acceso.
  El menú del Dock permite elegir una ventana y pulsar el icono recupera la última
  utilizada. Al cerrar todas esas ventanas, Jarvis continúa en el menubar. Los
  avisos, el HUD y las aprobaciones no activan por sí solos la presencia en el Dock.

## Navegación y contexto

La barra lateral presenta accesos estables a Nuevo chat, Buscar chats,
Programadas y Dot. Proyectos e historial tienen encabezados plegables con nombre,
estado y ayuda accesibles; su expansión es local a la barra abierta. La fila activa
mantiene un fondo visible y estado de selección accesible. Ajustes, archivados,
papelera e historial de acciones permanecen accesibles en el pie.

El historial normal agrupa las conversaciones cargadas en Hoy, Ayer, Últimos 7
días y Anteriores. Esta agrupación solo organiza la presentación: las búsquedas,
archivados y papelera conservan el orden recibido, sin reagruparse por fechas.
Los controles de carga y continuación de búsqueda permanecen fuera de las filas.

El lateral derecho muestra **un único panel a la vez**: navegador, chat lateral
o contexto del chat. Contexto reúne proyecto, archivos, privacidad y acceso al
historial de acciones. Abrir otro panel reemplaza el visible. En ventanas de
menos de 1.120 puntos de ancho, un panel derecho oculta temporalmente la barra
lateral para preservar espacio de lectura; cerrarlo permite recuperarla. El
botón «Mostrar historial» cierra el panel derecho y abre la navegación.

La paleta de comandos se abre con ⌘K o desde las opciones del chat. Filtra acciones
por nombre y palabras relacionadas, muestra los atajos disponibles y permite
selección con flechas, ejecución con Intro y cierre con Escape. La apariencia
deshabilitada refleja la disponibilidad real de cada acción. Abrir o cerrar la
paleta debe conservar el borrador de la conversación.

## Editor de mensajes

`JarvisChatComposerView` contiene una superficie propia centrada, de anchura
acotada, con adjuntos, editor multilínea y controles en el borde inferior. El
texto inicial varía según conversación, proyecto, modo de plan y respuesta en
curso. Los botones de adjuntar, modo, voz y envío tienen nombres accesibles; el
contorno refuerza el foco, la recepción de archivos y el contraste aumentado.

El editor utiliza el sistema de texto de AppKit dentro de SwiftUI. Intro y
⌘Intro envían; Mayús-Intro introduce una línea. Tab y Mayús-Tab recorren los
controles. Confirmar una composición de texto con un método de entrada no debe
enviar el mensaje, ni mantener Intro pulsado provocar varios envíos.

Durante una respuesta, el editor conserva el borrador y ofrece detener, poner
el texto en cola o corregir, según la disponibilidad de cada operación. El pie
explica el modo de plan, los límites del mensaje y el uso de los adjuntos. Las
acciones no disponibles deben permanecer claramente deshabilitadas; las
sugerencias preparan texto editable sin enviarlo automáticamente.

La [revisión REA de interfaz](quality/CODEX_REA_INTERFACE.md) documenta las ideas
observadas en el cliente distribuido de Codex y sus límites. Los componentes,
colores, textos y recursos de Jarvis tienen implementación propia.

## Revisión visual

Comprobar configuración y sus secciones, los dos paneles de voz, chat con y sin
historial y con adjuntos, y el menubar con ambas secciones expandidas. Comprobar
los diálogos de aprobación, navegador, información pendiente y recuperación
con las vistas de prueba aisladas (`--notch-preview`). Estas vistas no inician
el micrófono ni realizan solicitudes al servicio.

Validar cada ventana en su tamaño inicial y mínimo. Los textos largos deben
ajustarse dentro del contenido; títulos abreviados en listas conservan la ayuda
con el texto completo. La revisión de apariencia no cambia cuentas, permisos,
modelos asignados ni la voz actual.

Para el chat, revisar claro y oscuro con navegación abierta y plegada, grupos de
historial, búsqueda, editor vacío y con texto/adjuntos. Alternar contexto,
navegador y chat lateral en una ventana amplia y otra estrecha. Recorrer con el
teclado el editor, la paleta y las acciones, comprobando foco, borrador, estado
deshabilitado, selección y comportamiento de los métodos de entrada. Usar datos
sintéticos; estas comprobaciones son criterios de revisión, no una afirmación de
que la validación del nuevo diseño ya haya concluido.

Para el Dock, comprobar apertura desde el menubar, cambio entre dos ventanas,
recuperación de una ventana minimizada y cierre de la última. Abrir nuevamente
una escena cerrada debe recuperar el acceso sin crear duplicados. El cierre de
ventanas no debe detener el servicio ni la escucha habilitada.
