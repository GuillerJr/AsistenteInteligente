# ADR-0057: Lectura inicial IPC acotada

- Estado: aceptado
- Fase: 4 — Ciberseguridad
- Fecha: 2026-08-21

## Contexto

Una conexión admitida disponía de cinco segundos para enviar su frame. Un proceso local podía ocupar
los cupos duros abriendo sockets sin transmitir, o intentar prolongar la lectura mediante bytes
parciales.

Los clientes UDS de Jarvis construyen el JSON y lo escriben completo inmediatamente después de
conectar. No existe una operación remota ni interacción humana dentro de ese tramo.

## Decisión

El frame completo debe llegar en un segundo. `asyncio.wait_for` envuelve una única llamada a
`readline`, por lo que los bytes parciales no reinician el presupuesto. El valor se configura entre
100 ms y 5 s mediante `AEGIS_IPC_READ_TIMEOUT_SECONDS`.

## Filtro del algoritmo de ingeniería

1. Se cuestionó el timeout heredado de cinco segundos para un socket local.
2. Se descartaron keepalive, protocolo incremental y watchdog adicional.
3. Se parametriza el timeout nativo ya existente.
4. El caso normal no añade operaciones y falla antes ante clientes lentos.
5. El `finally` de admisión recupera automáticamente el cupo al vencer.

## Consecuencias

Una conexión o frame parcial retiene capacidad como máximo durante el presupuesto configurado. El
timeout de respuesta de los clientes y el timeout de handlers siguen siendo límites independientes.
