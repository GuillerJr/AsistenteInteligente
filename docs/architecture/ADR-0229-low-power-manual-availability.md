# ADR-0229: Bajo consumo limita únicamente la escucha automática

- Estado: aceptado
- Fecha: 2026-10-02
- Reemplaza la suspensión global por bajo consumo; mantiene la protección térmica.

## Decisión

El estado energético conserva `low_power_mode` como una observación real de macOS, pero la
disponibilidad del daemon depende únicamente del estado térmico. En temperatura nominal o moderada,
chat, CLI, Telegram, herramientas, aprobaciones y voz iniciada manualmente permanecen disponibles.
Activar bajo consumo no cancela una solicitud ni cierra su sesión manual de audio.

`WakeWordEnergyPolicy` conserva la pausa existente del detector continuo y la activación por nombre.
Desactivar bajo consumo permite su reanudación mediante la compuerta acústica existente. No se añade
una preferencia ni se cambia automáticamente la configuración energética de macOS.

Las transiciones nativas de bajo consumo se envían por `audio.session.resume`, con su causa y
observación reales. El daemon sigue aceptando la forma anterior de `audio.session.close` por
compatibilidad, pero cierra audio únicamente si el estado térmico requiere suspensión. Rechaza una
secuencia antigua antes de modificar el audio. Una recuperación térmica admite que bajo consumo
siga habilitado; una transición `low_power_disabled` continúa exigiendo el indicador desactivado.

Temperaturas serias, críticas o desconocidas mantienen la suspensión térmica. Reposo del equipo,
integridad, permisos, identidad y aprobaciones conservan sus propias condiciones. La certificación
de activación por voz requiere que el detector esté disponible, aunque el servicio manual esté activo.

## Validación

Pruebas de IPC y estado energético verifican la conservación de audio manual, rechazo de secuencias
antiguas y recuperación térmica con bajo consumo activo. Las pruebas de Telegram verifican nuevas
solicitudes y entrega de trabajos en curso durante bajo consumo, y la pausa por temperatura seria.
Las pruebas nativas verifican el contrato de estado activo y la pausa exclusiva de la palabra de
activación. El mantenimiento de memoria sigue usando su límite térmico sin una pausa por bajo consumo.
