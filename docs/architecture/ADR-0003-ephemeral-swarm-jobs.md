# ADR-0003: trabajos efímeros del enjambre

- Estado: aceptado
- Fecha: 2026-08-18

## Contexto

Una ejecución del enjambre puede tardar más que una interacción IPC y puede necesitar cancelación.
Mantener abierta la conexión durante llamadas a NVIDIA dificultaría aplicar timeouts, recuperación
del HUD y apagado ordenado del daemon.

## Decisión

1. `swarm.submit` validará la solicitud y devolverá inmediatamente un UUID de trabajo.
2. `jobs.status` permitirá consultar estados `queued`, `running`, `completed`, `failed` y
   `cancelled`; `jobs.cancel` solicitará cancelación idempotente.
3. Los trabajos y resultados serán efímeros y vivirán solo en memoria. Al apagar el daemon, todos
   los trabajos activos se cancelarán antes de cerrar el cliente NVIDIA.
4. La capacidad será acotada. Al llenarse, se eliminarán primero los trabajos terminales más
   antiguos; si todos están activos, nuevas solicitudes serán rechazadas.
5. Las excepciones internas se reducirán a códigos estables. No se enviarán trazas, mensajes del
   proveedor, prompts internos ni credenciales por IPC.
6. El resultado público se limitará a 24 KiB de cadena JSON serializada para permanecer por debajo
   del frame máximo de 64 KiB incluso con caracteres multibyte, controles escapados y metadatos del
   envelope.
7. Cada trabajo invocará el mismo grafo con Tool Broker, confirmaciones de un solo uso y auditoría.
   Añadir jobs no concede capacidades nuevas ni crea una ruta de ejecución directa.

## Consecuencias

- El HUD puede enviar una solicitud, cerrar su conexión y continuar consultando el progreso.
- Cancelar un job propaga cancelación al grafo y a la llamada HTTP asíncrona en curso.
- Un reinicio pierde el historial de jobs; la persistencia conversacional implementada en ADR-0006
  permanece separada de esta cola operacional.
