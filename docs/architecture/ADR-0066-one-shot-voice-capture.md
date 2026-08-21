# ADR-0066: Captura de voz de un solo uso

- Estado: aceptado
- Fases: 3 — Capacidades sensoriales; 4 — Ciberseguridad
- Fecha: 2026-08-21

## Contexto

El nonce autenticado impide repetir el mismo frame IPC, pero un cliente podía enviar el mismo
transcript y `capture_id` dentro de un frame nuevo. Eso creaba otro job y podía duplicar una llamada
NVIDIA o una solicitud de aprobación.

## Decisión

`voice.submit` consume cada `capture_id` válido una sola vez. El daemon conserva únicamente los 256
UUID más recientes en memoria; no almacena transcript, respuesta ni estado en disco. La comprobación
y la inserción ocurren antes del primer `await`, por lo que dos entregas concurrentes no atraviesan
la frontera en el mismo proceso.

## Filtro del algoritmo de ingeniería

1. Se cuestionó que autenticación de transporte equivaliera a idempotencia de captura.
2. Se descartaron SQLite, timestamps, locks y persistencia.
3. Un `deque` acotado usa el identificador ya presente en el contrato.
4. La búsqueda máxima recorre 256 UUID y la expulsión es automática.
5. Todo productor de voz hereda semántica *at-most-once* sin coordinación adicional.

## Consecuencias

Un reenvío recibe `voice_capture_replayed` y no crea un job. Tras 256 capturas nuevas o un reinicio,
el UUID sale de la ventana; la protección criptográfica de nonce y frescura IPC sigue aplicándose.
Si una entrega válida alcanza una cola saturada, esa captura permanece consumida y el usuario debe
iniciar un turno nuevo, evitando reintentos ambiguos de acciones tácticas.
