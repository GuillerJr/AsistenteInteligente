# ADR-0006: conversaciones persistentes y ordenadas

- Estado: aceptado
- Fecha: 2026-08-18

## Contexto

La memoria RAG conserva hechos y preferencias, pero no representa por sí sola una conversación. El
asistente necesita continuidad explícita entre solicitudes y reinicios sin convertir cada mensaje
en memoria semántica ni permitir que un prompt seleccione namespaces arbitrarios.

## Decisión

1. El esquema SQLite v3 añadirá conversaciones y turnos `user`/`assistant`. Cada turno tendrá UUID,
   secuencia monotónica, timestamp y hash del contenido.
2. `conversations.create`, `conversations.history` y `conversations.delete` operarán exclusivamente
   por IPC autenticado. El namespace será configuración del daemon.
3. `swarm.submit` aceptará un `conversation_id` opcional. El daemon verificará su existencia antes
   de crear el job y devolverá el ID en todos sus snapshots.
4. Los jobs de una misma conversación se serializarán con un lock local. El historial se leerá
   después de adquirirlo y el intercambio completo se persistirá en una única transacción solo tras
   obtener una respuesta válida.
5. Una cancelación antes de terminar el grafo no persistirá turnos. Una cancelación recibida después
   de iniciar la transacción de persistencia se linealizará como finalización: el commit protegido
   terminará y el job quedará completado.
6. Conversaciones, turnos, historial recuperado y bytes inyectados tendrán límites configurables.
   Alcanzar capacidad producirá un error estable y nunca expulsión silenciosa.
7. El historial enviado por IPC limitará cada turno a 4 KiB de JSON serializado y un máximo de diez
   turnos. El historial destinado a LangGraph tendrá un presupuesto independiente de 4 KiB y se
   marcará como contexto no confiable.
8. `swarm.submit` rechazará patrones evidentes de credenciales antes de invocar al proveedor. El
   filtro de persistencia seguirá activo como defensa secundaria para llamadas internas o contenido
   generado; en ese caso el job expondrá `conversation_persisted=false`.
9. Al reanudar una sesión, el historial acotado se enviará al modelo NVIDIA especialista. Esta
   transferencia es inherente al procesamiento remoto y se documentará de forma explícita.

## Consecuencias

- Una conversación puede reanudarse tras reiniciar el daemon usando su UUID.
- Dos solicitudes simultáneas de la misma sesión no pueden invertir el orden del historial.
- La eliminación usa la misma política SQLite `secure_delete` y borra turnos por cascada.
- El historial no concede permisos, herramientas ni capacidad de cambiar políticas; sigue siendo
  entrada potencialmente hostil.
