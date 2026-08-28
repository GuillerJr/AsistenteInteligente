# ADR-0192: caducidad event-driven de aprobaciones

## Estado

Aceptada.

## Contexto

El daemon caduca correctamente una confirmación a los dos minutos, pero la app dejaba de esperar el
job después de mostrar `awaiting_confirmation`. Sin otra interacción, podía conservar una ventana y
un estado naranja obsoletos. Los fallos terminales también cambiaban el notch a error sin explicar
por voz qué había ocurrido.

## Decisión

1. Al recibir una aprobación, la app programa una sola tarea hasta `expires_at`, con 50 ms de margen.
2. El retraso se limita entre 50 ms y 125 segundos; una fecha remota no mantiene una tarea ilimitada.
3. Al despertar, si siguen coincidiendo job, digest, sesión y credencial, la app consulta el estado
   por IPC autenticado. No aprueba ni cancela la acción.
4. Aprobar, denegar, interrumpir, bloquear la sesión o completar el job cancela esa tarea.
5. La expiración retira la aprobación y se anuncia con una frase local. Otros fallos usan un mapa
   determinista acotado; cancelaciones por interrupción permanecen silenciosas y ningún código
   interno se pronuncia.

## Consecuencias

- La interfaz ya no conserva aprobaciones vencidas.
- No se añade polling ni actividad periódica en segundo plano.
- La voz explica el cierre del flujo sin ampliar autoridad ni aceptar confirmación hablada.
