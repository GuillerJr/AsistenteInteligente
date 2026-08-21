# ADR-0062: Lease de sesión de audio

- Estado: aceptado
- Fase: 3 — Capacidades sensoriales
- Fecha: 2026-08-21

## Contexto

La telemetría permite una sola sesión. Si el helper o la aplicación terminaban antes de ejecutar
`audio.session.close`, esa sesión permanecía activa hasta reiniciar el daemon y bloqueaba capturas
posteriores.

## Decisión

Una sesión dispone de una lease de inactividad de cinco segundos. `open` usa su instante de creación
y cada `publish` renueva la actividad. `open`, `publish`, `status` y `close` eliminan primero una
sesión cuya lease venció. Publicar o cerrar después del vencimiento devuelve el error estable de
sesión inexistente.

La lease debe ser mayor que el umbral de telemetría obsoleta, que permanece en un segundo.

## Filtro del algoritmo de ingeniería

1. Se cuestionó que todo cliente alcanzara siempre su bloque de cierre.
2. Se descartaron heartbeat adicional, timer y tarea periódica.
3. Se comparan dos timestamps dentro del bloqueo ya existente.
4. No hay trabajo en segundo plano ni sondeo nuevo.
5. La limpieza ocurre automáticamente en el siguiente acceso al manager.

## Consecuencias

Un proceso sensorial caído retiene la sesión como máximo durante cinco segundos de inactividad. Una
captura sana publica con mucha mayor frecuencia y renueva la lease sin cambiar su payload.
