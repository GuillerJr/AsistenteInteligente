# ADR-0137: ejecución nativa ligada a la sesión macOS activa

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Decisión

1. La app mantiene una compuerta local de ejecución con una generación monotónica por sesión
   activa. Cada espera de `computer.wait`, envío hablado y aprobación captura un permiso de esa
   generación antes de abandonar el actor principal.
2. `NSWorkspace.sessionDidResignActiveNotification` cierra la compuerta antes de cancelar el job.
   El cierre incrementa la generación, invalida esperas y respuestas anteriores y ejecuta una sola
   cancelación sobre el helper activo.
3. El helper se crea y registra bajo la misma exclusión. Si el bloqueo ocurre antes del arranque, no
   se lanza; si ocurre después, Jarvis envía `terminate` al proceso firmado y falla cerrado como
   `user_session_inactive`.
4. Desbloquear permite una generación nueva, pero nunca rehabilita un permiso anterior. Una orden,
   aprobación, submission o animación del puntero nacida antes del bloqueo continúa inválida.
5. Si `voice.submit` o `jobs.approve` termina después de cruzar un bloqueo, la app cancela el
   `job_id` recién recibido y no lo adopta como activo.
6. Mientras la sesión está inactiva, Jarvis omite la inspección periódica del helper y de TCC. El
   daemon y su auditoría pueden seguir comprobándose sin pantalla, puntero ni acción nativa.
7. El `NSApplicationDelegate` registra los eventos en `applicationWillFinishLaunching`. Esto cubre
   un relanzamiento dentro de una sesión ya inactiva usando el evento que AppKit garantiza antes de
   `didFinishLaunching`, sin consultar claves privadas del WindowServer.

## Motivo

El relay espera hasta 20 segundos sin ocupar CPU. Un booleano consultado antes de esa espera no
demuestra que la sesión siga activa cuando llega la orden; además, bloquear y desbloquear con rapidez
puede hacer que una respuesta antigua observe de nuevo un valor `true`. Una generación local elimina
ambas carreras sin polling, XPC, estado durable ni una dependencia adicional.

## Consecuencias

- Una acción que ya alcanzó macOS justo antes de la notificación no puede deshacerse, pero el helper
  recibe cancelación inmediata y ningún paso posterior atraviesa el bloqueo.
- El puntero de Jarvis no reaparece por callbacks anteriores al bloqueo.
- Una sesión nueva puede esperar a que termine el proceso cancelado anterior; se prioriza no ejecutar
  dos helpers en paralelo sobre reanudar con menor latencia.
- Los clientes Python conservan el error específico `user_session_inactive` para terminar el ciclo
  de control de forma segura y auditable.
