# App de escritorio de la variante propia

Requisito del dueño, 24-10-2026. Pertenece a la **fase A**, antes de integrar las
mejoras adicionales de Jarvis. Este documento define el cliente. Ya existe una
implementación de desarrollo en `native/JarvisDesktop`, con scripts propios y
pruebas de IPC. Su distribución autocontenida y cualificación completa siguen abiertas.

## Experiencia elegida

- Aplicación nativa macOS, implementada en SwiftUI con puentes AppKit acotados.
- Ventana principal propia para conversar, con navegación y estados de sesión.
- Icono en el Dock para abrir/enfocar la ventana y una entrada en la barra de menús.
- Atajo configurable para abrir/enfocar el chat; una invocación repetida reutiliza
  la ventana en vez de crear clientes o servicios duplicados.
- Cerrar la ventana y salir de la aplicación son acciones distintas y visibles.
- El cliente inicia y supervisa su servicio interno; el usuario no necesita abrir
  Terminal ni iniciar un servidor para conversar.
- Interfaz y mensajes de estado en español, accesibilidad y preferencias del sistema.
- Renderizado nativo del chat, actividad, recibos, errores y decisiones. El cliente
  no consiste en un navegador ni en una WebView que cargue el panel de OpenClaw.

Las páginas oficiales de inicio de sesión pueden abrirse en el navegador cuando
corresponda al proveedor elegido. Ese flujo puntual de autenticación es distinto
de la interfaz habitual de conversación. Contraseñas, TCC y decisiones de permiso
siguen correspondiendo al dueño.

## Cliente y servicio

El núcleo propio contiene sesiones, ejecuciones, política, eventos y persistencia.
La app consume una API propia tipada mediante transporte local autenticado. Ese
transporte y el arranque supervisado tienen una primera implementación real, con
autenticación por token privado. Falta cualificar identidad de cliente firmada,
distribución y recuperación completa de la app; el token no acredita esas garantías.

La interfaz representa estado confirmado por el núcleo:

- `cancel requested` no se presenta como limpieza terminada.
- El texto del modelo se distingue de los recibos de acciones.
- Un efecto incierto muestra esa condición y no invita a repetirlo automáticamente.
- Una aprobación describe operación, argumentos y alcance exactos; una tarjeta
  vieja o una reconexión no vuelve a conceder permiso.
- Si el servicio cae, la app informa la desconexión y recupera la sesión conforme
  al registro durable; no inventa éxito ni vuelve a ejecutar mutaciones.

La base mantiene `jarvis` en los identificadores técnicos que corresponda conservar.
La implementación del cliente será propia; el clon de referencia no se integra
como ejecutable, recursos visuales ni dependencia del producto.

## Criterios de cierre de A3

1. Abrir la app desde Finder/Dock muestra su ventana y arranca un solo servicio.
2. Dock, barra y atajo enfocan el mismo chat sin depender de pestañas del navegador.
3. Un chat con el proveedor elegido muestra estados, respuesta y errores veraces.
4. Cancelar y enviar otra consulta funciona mientras el cliente refleja asentamiento.
5. Reiniciar cliente/servicio recupera el estado que corresponda sin duplicar efectos.
6. Un cliente no autorizado no obtiene acceso al transporte ni a datos de sesión.
7. La interfaz del hito de chat se prueba sin añadir herramientas históricas. Las
   capacidades de computadora, audio, nodos y canales que ya pertenecen a OpenClaw
   siguen en fase A, según la matriz. Sus mejoras propias de Jarvis vendrán en B.

La comprobación será desde la app propia y fixtures autorizadas. Las pruebas de
contratos o capturas estáticas no sustituyen ese recorrido real.
