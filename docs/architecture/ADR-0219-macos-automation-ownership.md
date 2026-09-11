# ADR-0219: Propiedad y evidencia de automatización macOS

- Estado: aceptado
- Fecha: 2026-09-11
- Bloque: pulido 3 — automatización de macOS

## Decisión

Las operaciones nativas bloqueantes pertenecen a su tarea hasta que termina el worker. La
cancelación conserva la exclusión de la sesión y se propaga después del drenaje, sin interpretar
un evento ya enviado al sistema como reversible. La cola del enlace distingue una orden en espera
de una entregada, descarta caducidad y cancelación, acepta una sola respuesta antes de su plazo y
despierta a todos los consumidores al cerrar.

Un resultado no puede verificar una llamada distinta ni otra herramienta. Un plan con IDs
duplicados se rechaza antes de ejecutarse. `tools.verification` es el contrato común para el
reflector, el planificador, el runtime aprobado, sus métricas y la presentación del éxito visual.
El control visual necesita un booleano verdadero explícito, estado terminal completado y ningún
error. Las herramientas no visuales conservan compatibilidad cuando omiten evidencia opcional.

La recuperación de UI solo es elegible cuando la evidencia actual muestra un bloqueo incierto
antes del primer paso, sin error operativo. La intervención del usuario, los objetivos sensibles,
la ejecución parcial y los resultados ajenos son terminales; los recuerdos de un modal no
permiten reintentar. Recargar y reevaluar requieren la confirmación consumida de la acción padre.

## Verificación y consecuencias

El [bloque 3](../quality/MACOS_AUTOMATION_POLISH.md) documenta 17 reproducciones iniciales y 40
casos añadidos. Incluye cancelación reiterada, trabajo en cola, fallos tardíos, entrega/caducidad,
identidad de aplicación y llamada, evidencia contradictoria y ausencia de reintentos inseguros.
Las pruebas de la aplicación real permanecen aisladas de los datos personales.

Una operación síncrona incorporada debe limitar su I/O; el wrapper no puede matar un hilo de
Python. No se amplían autorizaciones ni se habilita inferencia local como efecto de esta decisión.
