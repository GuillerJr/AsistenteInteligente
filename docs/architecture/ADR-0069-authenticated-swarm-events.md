# ADR-0069: Eventos autenticados del enjambre

- Estado: aceptado
- Fase: 1 y 5 — Orquestación e interfaz visual
- Fecha: 2026-08-24

## Contexto

El HUD consultaba `swarm.activity` cuatro veces por segundo mientras estaba abierto. Ese sondeo
duplicaba respuestas aunque nada cambiara y no permitía que la presencia del notch reaccionara a
trabajos iniciados desde voz, terminal u otro cliente cuando el HUD estaba cerrado.

## Decisión

El tracker conserva un contador de versión monotónico en memoria. Cada entrada o salida real de un
agente incrementa la versión y despierta a los consumidores mediante `asyncio.Condition`.
`swarm.wait` recibe únicamente `after_version` y un timeout entre 100 y 20 000 ms. Devuelve versión,
indicador de cambio y los mismos contadores agregados y no sensibles de `swarm.activity`.

La llamada mantiene la frontera existente: socket Unix `0600`, UID del peer, HMAC, timestamp, nonce,
frame de 64 KiB y una respuesta firmada. Una excepción de timeout por método permite 22 segundos
solo a `swarm.wait`; los demás handlers conservan el límite de cuatro segundos. El latido sin cambio
reconecta de forma acotada y un fallo aplica backoff hasta cinco segundos.

La Menu Bar mantiene un único consumidor mientras vive la app. El HUD solo renderiza el estado y
continúa abriéndose bajo invocación explícita. El notch no gana controles ni hit testing: despierta
automáticamente y muestra el rol activo aun cuando el trabajo nació fuera de la UI.

## Filtro del algoritmo de ingeniería

1. Se cuestionó que una animación necesite sondeo a 4 Hz.
2. Se eliminaron el temporizador del HUD y las respuestas idénticas repetidas.
3. Se reutilizó el protocolo autenticado; no se añadieron WebSocket, puerto, broker ni dependencia.
4. Una transición despierta al cliente de inmediato en vez de esperar el siguiente intervalo.
5. Un solo monitor alimenta Menu Bar, notch y HUD, con recuperación automática por latido y backoff.

## Consecuencias

La latencia visual depende de la transición real del agente y el consumo en reposo baja a una
solicitud cada veinte segundos. Una espera ocupa uno de los cupos IPC mientras está activa; el cupo
duro existente limita el impacto y la respuesta siempre libera la conexión.
