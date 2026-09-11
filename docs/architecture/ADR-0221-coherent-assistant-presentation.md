# ADR-0221: Presentación coherente de Jarvis

- Estado: aceptado
- Fecha: 2026-09-11
- Bloque: notch, HUD y mensajes de estado

## Decisión

El notch, el encabezado del menú y el HUD consumen `AssistantPresentation`, una proyección pura
de la evidencia del modelo. Comparte título, resumen compacto, explicación, símbolo, tono y
política de animación. No ejecuta acciones, no agrega IPC y no sustituye las comprobaciones de
autorización del runtime.

La seguridad fallida tiene prioridad sobre conexión y turno. La conexión y la auditoría deben
estar verificadas antes de presentar disponibilidad; un fallo narrado sigue siendo un fallo.
Se separan interrupción, aprobación, selección, captura, procesamiento, respuesta y finalización.
Finalizar una respuesta no afirma que una herramienta haya tenido éxito; los resultados
inciertos piden revisar posibles cambios antes de repetir una acción. Los errores desconocidos
no pasan texto técnico a la pantalla ni a Accesibilidad.

El HUD conserva el grafo SceneKit pero incorpora texto legible sobre una superficie oscura,
controles nativos y cierre explícito sin cancelar trabajo. Su `NSPanel` no activa otra vez toda
la aplicación; la geometría se ajusta al área visible y a desconexiones de pantallas. El notch
sigue sin recibir foco ni eventos de ratón y solo existe ante un recorte físico verificable.

La reducción de movimiento detiene la línea de tiempo y SceneKit. Los estados estáticos no
mantienen renderizado continuo. El desmontaje libera la escena. Las pruebas visuales DEBUG
no arrancan daemon, micrófono, monitores ni autorizaciones; la versión release no las incluye.

## Verificación y límites

La proyección y la geometría se prueban en AegisAudioCore; los paneles se revisan mediante la
vista de pruebas integrada al flujo `script/build_and_run.sh --notch-preview`.
El [informe de interfaz](../quality/NOTCH_HUD_POLISH.md) registra alcance y comprobaciones.
No cambia el proveedor del CLI, la memoria, el protocolo IPC ni las políticas de autorización.
