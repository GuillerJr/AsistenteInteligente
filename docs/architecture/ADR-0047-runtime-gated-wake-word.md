# ADR-0047: Wake word condicionado por el runtime íntegro

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** bloquear un turno mientras SoundAnalysis sigue activo consume micrófono y CPU sin
   posibilidad de producir una respuesta.
2. **Eliminar:** no se añade health check, temporizador, conexión ni política paralela.
3. **Simplificar:** el sondeo existente combina `daemonState == online` y auditoría `intact` en un
   único booleano de disponibilidad.
4. **Acelerar:** se generaliza la política pura de TCC y se reutilizan `start` y `stop` existentes.
5. **Automatizar:** una caída pausa el detector y una recuperación real lo reanuda sin intervención.

## Decisión

El monitor conserva la disponibilidad anterior, actualiza permisos, consulta daemon e integridad y
aplica `WakeWordAvailabilityPolicy`. Para una activación habilitada, una transición a no disponible
cancela reanudaciones y recuperaciones, detiene el detector y publica `paused`. Con opt-out no cambia
el estado `off`, aunque cualquier detector residual se detiene por defensa. La preferencia del usuario
no se borra.

Una transición a disponible solo puede arrancar desde `paused` o `unavailable`, con Micrófono
autorizado y modelo válido. Los estados `failed` y `recovering` son inelegibles para impedir que el
sondeo periódico amplíe el único reintento permitido ante una avería real.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** elimina captura inútil cuando no existe ruta de procesamiento.
- **Fase 4 — Ciberseguridad:** detiene el sensor si la auditoría no es íntegra.
- **Fase 5 — Voice-first:** reanuda automáticamente después de recuperar el servicio confiable.

## Consecuencia

Jarvis deja de escuchar durante una caída o compromiso detectado. La latencia máxima para reflejar
un cambio externo sigue siendo el sondeo existente de diez segundos; no aparece trabajo de fondo
adicional.
