# ADR-0102: temporizador de voz nativo y efímero

- Estado: aceptado
- Fases: 3 y 5

## Decisión

1. La app nativa interpreta una gramática cerrada en español o inglés después de obtener una
   transcripción final on-device y antes de enviarla al daemon.
2. Solo existen tres operaciones: iniciar un temporizador numérico de entre un segundo y 24 horas,
   consultar su tiempo restante o cancelarlo. Solo puede existir uno a la vez.
3. `AegisAudioCore` aloja el parser puro y un scheduler `@MainActor` basado en una única `Task`
   cancelable, `Task.sleep` y un deadline de `ContinuousClock`. Consultar calcula el remanente bajo
   demanda; no se bloquea un hilo ni se añade proceso, dependencia o sondeo.
4. Inicio, rechazo y cancelación reutilizan la salida de voz y el aislamiento del wake word. Al
   vencer siempre se emite un beep; la locución solo ocurre si no reemplaza una captura, procesamiento,
   respuesta o aprobación existente.
5. El temporizador vive únicamente en memoria. Terminar o actualizar la app lo cancela; no existe
   historial, restauración, notificación del sistema, IPC, red, modelo, RAG ni permiso adicional.
6. La telemetría registra únicamente evento y duración numérica; nunca conserva el transcript.

## Motivo

Programar una espera local no requiere un LLM ni autoridad del daemon. Resolverla en el mismo proceso
que ya captura y reproduce voz elimina latencia y evita sincronización adicional. La restricción a un
temporizador cubre el caso cotidiano sin introducir una base de datos, una cola o una interfaz nueva.

## Límites

- No interpreta números escritos con palabras, decimales, fechas, alarmas, etiquetas ni repeticiones.
- No sobrevive al cierre, reinicio o actualización de `Jarvis.app`.
- Si vence durante otro turno, el beep avisa sin alterar su estado; no se difiere una segunda locución.
- Una frase que no coincide por completo conserva el cerebro híbrido habitual.
