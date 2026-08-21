# ADR-0056: Cupo duro de admisión IPC

- Estado: aceptado
- Fase: 4 — Ciberseguridad
- Fecha: 2026-08-21

## Contexto

El semáforo limitaba el procesamiento simultáneo, pero cada conexión aceptada creaba una coroutine
que esperaba fuera del cupo. Una ráfaga o varios clientes lentos podían acumular sockets y tareas
sin una cota equivalente a `max_clients`.

## Decisión

El daemon mantiene un contador de conexiones admitidas. La comprobación y el incremento ocurren
antes del primer `await`, por lo que son atómicos dentro del único event loop. Si el cupo está lleno,
la conexión se cierra antes de resolver credenciales, leer datos, autenticar o tocar la ventana de
nonces. No existe cola interna de admisión.

Las pruebas de capacidad de jobs usan suficientes conexiones porque su objetivo independiente es
medir la cola de trabajos, no la frontera de sockets.

## Filtro del algoritmo de ingeniería

1. Se cuestionó que un semáforo acotara también los clientes que esperaban adquirirlo.
2. Se eliminó la cola implícita y el objeto `Semaphore`.
3. Se usan un entero y la serialización natural del event loop.
4. La saturación falla inmediatamente y el camino normal no añade esperas.
5. El contador se libera en `finally` para toda salida y excepción.

## Consecuencias

El proceso conserva como máximo `max_clients` conexiones admitidas. Las conexiones excedentes deben
reintentar desde el cliente; Jarvis prioriza una degradación rápida y acotada sobre espera invisible.
