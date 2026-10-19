# Diálogos de interacción de Jarvis

Jarvis reúne las decisiones de la tarea actual en una ventana normal de macOS,
con el diseño compartido del menubar y las ventanas de voz. Se activa al requerir
una respuesta y cede el foco cuando el usuario cambia de ventana. No utiliza un
nivel flotante ni bloquea otras aplicaciones.

## Interacciones

- Aprobación: resumen, todos los argumentos normalizados, contenido completo,
  advertencia del proveedor, caducidad y botones con la acción concreta.
- Corrección: detiene la autorización por voz/Touch ID mientras se escribe.
  Cancelar conserva la solicitud sin aprobarla. Evaluar cancela primero la tarea
  anterior y, solo tras confirmar su cancelación, envía una solicitud nueva por
  el IPC normal; no reutiliza su permiso. Si la cancelación falla, muestra el
  problema y no envía la corrección.
- Selección: navegadores descubiertos por el servicio, con su estado abierto,
  selección por botón o por la ruta de voz existente y cancelación explícita.
- Aclaración: una pregunta breve y explícita de una tarea completada sin ejecutar
  herramientas. Las preguntas sociales y los resultados de herramientas no crean
  formularios. La respuesta, por teclado o voz, se evalúa como nueva solicitud de
  texto junto con la solicitud original y la pregunta; no se inventa una captura
  de voz ni se heredan pruebas biométricas. El formulario vence en cinco minutos.
- Recuperación: explicación del problema, comprobar conexión/permisos y acceso a
  Configuración. Una acción con resultado incierto no se repite automáticamente.

El HUD y el menubar permiten volver a abrir la decisión pendiente. Cerrar la
ventana cancela la interacción; no equivale a aceptar. La aprobación mantiene su
digest exacto, sus controles de sesión y su consumo de una sola vez. El teclado
usa Escape para cancelar y Comando+Intro para enviar o aprobar explícitamente.

## Límites y comprobación

La vista previa se construye localmente a partir de `normalized_arguments`, sin
otra llamada de IA. Sus campos se validan y caben en el IPC; datos demasiado
grandes o con credenciales hacen fallar la preparación antes de emitir una
aprobación. El cliente rechaza una vista previa parcial o malformada.

Las pruebas cubren contenido multilínea sin truncar, límites de tamaño, rechazo
de credenciales, detección y caducidad de aclaraciones, exclusión de preguntas
sociales/resultados de herramientas, estados IPC y autorización de una sola vez.
Las vistas previas `approval`, `browser`, `input` y `recovery` muestran datos
ficticios y desactivan sus acciones: no solicitan micrófono ni realizan operaciones
en cuentas reales.
