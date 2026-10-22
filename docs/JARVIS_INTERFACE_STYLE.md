# Interfaces de Jarvis

La interfaz usa la paleta «Cálido»: fondo hueso en modo claro, carbón en oscuro,
tinta de alto contraste y acento arcilla. Los colores adaptativos viven en
`JarvisTheme.swift`. `JarvisInterfaceStyle.swift` reúne la cabecera de sección,
las superficies interiores, los botones y la disposición de interruptores.

## Convenciones

- Las ventanas con título conservan los bordes y esquinas de macOS. Las tarjetas
  interiores usan radios continuos de 12 puntos; no añaden un segundo marco a
  la ventana. El HUD conserva su máscara de panel independiente.
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
- En chat, la cabecera reduce «Nueva conversación» a un icono cuando hace falta;
  conserva la ayuda y el atajo ⌘N. El editor indica foco y destino de archivos.
  El historial distingue la ausencia de chats, la carga y una búsqueda vacía.
- Reducir transparencia y aumentar contraste siguen las preferencias de macOS.
  La presencia junto al notch también refuerza texto y contorno con alto contraste.
- Jarvis aparece en el Dock y en ⌘Tab mientras Chat, Configuración o un panel de
  voz estén abiertos. Minimizar una ventana u ocultar la app conserva ese acceso.
  El menú del Dock permite elegir una ventana y pulsar el icono recupera la última
  utilizada. Al cerrar todas esas ventanas, Jarvis continúa en el menubar. Los
  avisos, el HUD y las aprobaciones no activan por sí solos la presencia en el Dock.

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

Para el Dock, comprobar apertura desde el menubar, cambio entre dos ventanas,
recuperación de una ventana minimizada y cierre de la última. Abrir nuevamente
una escena cerrada debe recuperar el acceso sin crear duplicados. El cierre de
ventanas no debe detener el servicio ni la escucha habilitada.
