# ADR-0055: Despacho IPC acotado

- Estado: aceptado
- Fase: 4 — Ciberseguridad
- Fecha: 2026-08-21

## Contexto

El socket limita clientes concurrentes y tiempo de lectura, pero un handler interno podía esperar
indefinidamente. Aunque el cliente agotara su timeout, la coroutine del servidor seguía ocupando un
cupo y varias solicitudes atascadas podían dejar el control plane sin capacidad.

## Decisión

Cada handler personalizado dispone por defecto de cuatro segundos para validar y despachar una
solicitud. `asyncio.wait_for` cancela la coroutine al vencer y el daemon responde con el código
autenticado `handler_timeout`. El valor puede ajustarse entre 100 ms y 30 s mediante
`AEGIS_IPC_HANDLER_TIMEOUT_SECONDS`.

Los métodos integrados de salud y métricas no crean tareas externas. Los jobs conservan su propio
presupuesto de ejecución de 120 segundos porque el handler solo los encola o consulta.

## Filtro del algoritmo de ingeniería

1. Se cuestionó que el timeout del cliente detuviera trabajo dentro del servidor.
2. Se descartaron watchdog, proceso supervisor y cola IPC adicionales.
3. Se usa el timeout nativo de `asyncio` alrededor del único punto extensible.
4. El camino exitoso añade una sola envoltura sin sondeos ni I/O.
5. La cancelación y liberación de capacidad ocurren automáticamente.

## Consecuencias

Un handler bloqueado ya no retiene indefinidamente el semáforo del daemon. Los handlers deben
limitarse a validación y despacho; el trabajo prolongado pertenece al gestor asíncrono de jobs.
