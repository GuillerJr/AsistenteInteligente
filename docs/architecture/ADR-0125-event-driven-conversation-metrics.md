# ADR-0125: conversación dirigida por eventos y evaluación persistente privada

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. `jobs.wait` mantiene una espera IPC autenticada de hasta 20 segundos y despierta al aparecer un
   fragmento, una confirmación o un estado terminal. La app deja de consultar el job cada 80–400 ms.
2. Las evaluaciones terminales se guardan en `evaluations.sqlite3`, separado de la memoria personal.
   El archivo exige propietario local, permisos privados, identidad estable y esquema conocido.
3. Cada fila contiene solo el identificador aleatorio del job, fecha y métricas ya acotadas por
   `JobEvaluation`. No contiene solicitudes, respuestas, argumentos, nombres ni audio.
4. La retención predeterminada es 10 000 evaluaciones y elimina automáticamente las más antiguas.
5. La salida vocal puede cortar por oración, cláusula larga o tamaño máximo y prepara únicamente el
   siguiente segmento NVIDIA. Si ese segmento no está disponible 350 ms después del relevo, se usa
   la voz local para el resto de la respuesta.

## Motivo

El sondeo frecuente consume ciclos y añade retraso justo donde un asistente voice-first debe parecer
inmediato. Una sola espera larga y un solo prefetch reducen trabajo sin incorporar WebSockets,
frameworks o dependencias. La evaluación longitudinal permite decidir con evidencia si una mejora
realmente reduce p95 y aumenta la tasa de acciones verificadas.

## Límites

- Los jobs activos siguen siendo efímeros y no se restauran después de reiniciar el daemon.
- La telemetría nunca amplía autoridad, cambia políticas ni se transmite a NVIDIA.
- El prefetch conserva el orden, no sintetiza más de un segmento futuro y se cancela al interrumpir.
- El micrófono y el wake word conservan su política local y opt-in existente.
