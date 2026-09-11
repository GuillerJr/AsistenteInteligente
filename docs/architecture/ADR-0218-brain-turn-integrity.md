# ADR-0218: Integridad de turnos y trabajo paralelo supervisado

- Estado: aceptado
- Fecha: 2026-09-11
- Bloque: pulido 2 — cerebro y orquestación

## Decisión

Una generación terminada por límite o filtro no constituye una respuesta completa ni una
propuesta ejecutable. El transporte NVIDIA exige `stop` y `[DONE]` para texto en streaming;
conserva los eventos de uso independientes y deja de leer al recibir `[DONE]`. Una respuesta
incompleta produce `IncompleteModelResponseError`, un error distinto de indisponibilidad,
autenticación o cuota. No causa fallback ni continuación automática. La validación compartida
en `providers.base` se aplica antes de autorizar y antes de persistir. Vive bajo proveedores para
evitar una dependencia circular entre el paquete del cerebro y su transporte NVIDIA.

Los grupos de operaciones por petición se unen mediante `gather_owned`: propagan el primer
error y cancelan/drenan el resto. Esto mantiene intacta la clasificación de fallos existente.
El revisor es opcional ante errores operativos, pero su cancelación sigue siendo una señal de
control. El principal no puede degradarse a «resultado ausente». La espera de primer fragmento
remoto posee explícitamente tanto su petición como su tarea de espera.

## Verificación y consecuencias

Las pruebas del [bloque 2](../quality/BRAIN_ORCHESTRATION_POLISH.md) reproducen cortes de transporte,
agotamiento de tokens, cancelaciones antes del primer fragmento, principal fallido con asesor
pendiente y recuperación de memoria parcialmente fallida. Comprueban tareas drenadas, ausencia
de autorización sobre propuestas incompletas, historial sin turnos fallidos y recuperación de
la misma sesión. Se mantienen los límites de modelos, herramientas y privacidad del ADR-0217.

Las instantáneas parciales ya mostradas pueden existir hasta que se comunica el fallo. Nunca se
promocionan a una respuesta final correcta por el mero hecho de haber entregado texto. Los
proveedores personalizados que omiten `finish_reason` conservan compatibilidad; los motivos
explícitos `length` y `content_filter` se rechazan en las fronteras comunes.
