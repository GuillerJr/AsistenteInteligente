# ADR-0148: guardia contra repetición visual sin progreso

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

Después de una acción, `computer_use` vuelve a capturar la aplicación y consulta visión. Si la UI no
cambiaba y el modelo devolvía la misma acción, el controlador podía ejecutarla otra vez hasta agotar
el límite de pasos. Repetir escritura, teclas o clics sin evidencia de progreso no aporta autonomía
y puede producir efectos no deseados.

## Decisión

1. Cada observación remota obtiene una huella SHA-256 en memoria que incluye JPEG y percepción local.
2. El controlador conserva únicamente la última huella y la última acción ejecutada durante esa
   sesión aprobada.
3. Si ambas se repiten exactamente, termina con `blocked/uncertain_state` antes de ejecutar la acción.
4. Una imagen o árbol Accessibility diferente permite repetir la acción; desplazarse varias veces
   sigue funcionando cuando existe progreso visible.
5. `wait`, `done` y `blocked` no se consideran acciones repetibles. La huella no se persiste, registra
   ni envía como dato adicional al proveedor.

## Consecuencia

Jarvis deja de insistir sobre una interfaz estancada y evita duplicar texto o activaciones. La
comparación es local, constante en espacio y no añade otra captura, inferencia, dependencia o permiso.
