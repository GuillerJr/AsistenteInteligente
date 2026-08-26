# ADR-0106: guía local de capacidades por voz

- Estado: aceptado
- Fases: 3, 4 y 5

## Decisión

1. Una gramática cerrada reconoce preguntas exactas como «¿Qué puedes hacer?» después de la
   transcripción final on-device y antes de enviar el transcript mediante `voice.submit`. El
   preflight normal del turno permanece intacto.
2. Jarvis responde con un resumen fijo y acotado de conversación, memoria, lecturas y acciones. La
   variante se elige únicamente desde el estado nativo existente de Pantalla y Control.
3. Si el control visual está listo, se incluye como capacidad sujeta a aprobación. Si no lo está,
   Jarvis explica el requisito sin abrir Ajustes ni solicitar permisos automáticamente.
4. Temporizador, aplicación activa, identidad y esta guía comparten un único normalizador de frases.
   Las coincidencias siguen siendo completas; una pregunta compuesta conserva el cerebro híbrido.
5. No se añade proceso, dependencia, red, modelo, persistencia, permiso, memoria, catálogo dinámico,
   método IPC o sondeo. Los logs conservan solo el estado booleano del control visual.

## Motivo

El sistema ya dispone de capacidades amplias, pero descubrirlas no debe consumir una inferencia ni
obligar a abrir el manual. Una respuesta local ofrece latencia mínima y evita que un modelo prometa
acciones fuera de la política real.

## Límites

- El resumen describe categorías estables y no garantiza que una aplicación o servicio concreto
  esté disponible.
- Toda acción conserva broker, política, confirmación de un solo uso y auditoría.
- La guía no concede permisos, no ejecuta acciones y no revela credenciales o detalles internos.
