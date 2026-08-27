# ADR-0148: guardia contra repetición visual sin progreso

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

Después de una acción, `computer_use` vuelve a capturar la aplicación y consulta visión. Si la UI no
cambiaba y el modelo devolvía la misma acción, el controlador podía ejecutarla otra vez hasta agotar
el límite de pasos. Repetir escritura, teclas o clics sin evidencia de progreso no aporta autonomía
y puede producir efectos no deseados.

## Decisión

1. Cada observación lleva una firma visual dHash nativa de 256 bits y obtiene un SHA-256 local de su
   semántica Accessibility.
2. La semántica incluye ventanas, roles, etiquetas y estados accionable/sensible. Excluye OCR,
   coordenadas, confianza, orden de ventanas y truncamiento para no convertir jitter en progreso.
3. El controlador conserva únicamente ambas firmas y la última acción durante la sesión aprobada.
4. Repetir una acción exige un cambio semántico o una distancia Hamming visual mínima de 8 bits. En
   caso contrario termina con `blocked/uncertain_state` antes de ejecutar.
5. `wait`, `done` y `blocked` no se consideran acciones repetibles. Ninguna firma se persiste,
   registra ni añade al contexto del proveedor.

## Consecuencia

Jarvis deja de insistir sobre una interfaz estancada y evita duplicar texto o activaciones. La
comparación es local, constante en espacio y no añade otra captura, inferencia, dependencia o permiso.
