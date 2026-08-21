# ADR-0058: Respuesta IPC acotada

- Estado: aceptado
- Fase: 4 — Ciberseguridad
- Fecha: 2026-08-21

## Contexto

Las solicitudes y lecturas estaban acotadas, pero `writer.drain()` no tenía timeout. Además, un
handler que produjera un payload mayor al frame provocaba el cierre sin una respuesta autenticada.

## Decisión

La escritura completa dispone de un segundo, configurable entre 100 ms y 5 s con
`AEGIS_IPC_WRITE_TIMEOUT_SECONDS`. Si una respuesta exitosa excede el frame, el daemon descarta su
payload y devuelve el error pequeño y firmado `response_too_large`. La conexión conserva una sola
respuesta y siempre se cierra después.

## Filtro del algoritmo de ingeniería

1. Se cuestionó que limitar la lectura también limitara la escritura.
2. Se descartaron streaming, fragmentación y buffers adicionales para el control plane.
3. Se reutilizan `asyncio.wait_for` y el envelope de error existente.
4. El camino normal añade solo una comparación de longitud.
5. El timeout libera automáticamente el cupo mediante el `finally` de admisión.

## Consecuencias

Un cliente que no consume la respuesta no retiene capacidad indefinidamente. Los handlers deben
publicar resultados dentro del frame; los resultados grandes pertenecen a almacenamiento acotado o
a una interfaz futura explícitamente diseñada para streaming.
