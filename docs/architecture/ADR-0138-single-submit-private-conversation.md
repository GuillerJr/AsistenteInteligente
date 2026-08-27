# ADR-0138: conversación privada resuelta en un único envío

- Estado: aceptado
- Fases: 1, 2, 3 y 5

## Decisión

1. `voice.submit` e `image.submit` aceptan el booleano opcional `persist_conversation`. La app lo
   activa solo para un turno con propietario vocal y presencia local verificados.
2. El mismo handler reutiliza el `conversation_id` recibido o crea uno cuando falta o ya no existe.
   El snapshot inicial devuelve el UUID resuelto; no hay una llamada previa a
   `conversations.create` ni un reenvío del `capture_id`.
3. Una solicitud de audio que pide persistencia sin la prueba completa del propietario falla antes
   de crear una conversación o despachar el grafo.
4. Un turno no verificado omite el indicador y el UUID. Se procesa de forma efímera y no deja una
   conversación aislada huérfana en SQLite.
5. El modo exacto anterior se conserva para otros clientes: un `conversation_id` sin
   `persist_conversation` debe existir o devuelve `conversation_not_found`.
6. La resolución ocurre dentro del daemon y añade el UUID definitivo al contexto local. No cambia
   el payload minimizado enviado a NVIDIA ni las confirmaciones de herramientas.

## Motivo

El primer turno privado hacía dos viajes IPC y la recuperación de un UUID borrado intentaba enviar
dos veces la misma captura, contradiciendo la defensa antirreplay. Los turnos invitados también
creaban conversaciones que la app nunca reutilizaba. Resolver la continuidad en el envío ya
autenticado elimina esas tres rutas sin añadir un endpoint, proceso, dependencia o caché.

## Consecuencias

- El primer turno, la rotación por timeout y la recuperación de una sesión borrada necesitan un
  solo viaje por el socket antes de comenzar el job.
- El UUID que persiste la app es siempre el aceptado por el daemon, no uno creado de antemano.
- Una conversación puede quedar creada si el daemon pierde capacidad justo después de resolverla;
  conserva los límites existentes y no contiene turnos parciales.
- La API explícita `conversations.create` permanece disponible para clientes que administran
  conversaciones fuera del flujo de voz.
