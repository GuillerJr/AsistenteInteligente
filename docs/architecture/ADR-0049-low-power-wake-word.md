# ADR-0049: Wake word alineado con Modo de bajo consumo

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** mantener clasificación continua contradice la decisión explícita del usuario de
   reducir consumo energético.
2. **Eliminar:** Jarvis no añade preferencia, monitor de batería, temporizador ni cálculo propio.
3. **Simplificar:** se usa `ProcessInfo.isLowPowerModeEnabled` y su notificación nativa.
4. **Acelerar:** una sola función aplica transiciones de permiso, runtime, térmica y energía.
5. **Automatizar:** activar el modo pausa SoundAnalysis; desactivarlo agenda reanudación estable.

## Decisión

`WakeWordEnergyPolicy` permite escucha únicamente cuando Modo de bajo consumo está desactivado. La
Menu Bar registra `.NSProcessInfoPowerStateDidChange` junto con los observadores ya
existentes y consulta el valor actual solo cuando macOS publica un cambio o el equipo despierta.

Las cuatro fuentes de disponibilidad convergen en `reconcileWakeWordAvailability`: misma política
pura, cancelación, stop y compuerta acústica. TCC conserva `unavailable`; runtime, presión térmica y
energía usan `paused`. No se amplía la recuperación de errores ni se borran preferencias.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** reduce actividad continua cuando el sistema prioriza batería.
- **Fase 5 — Voice-first:** respeta la política macOS y mantiene caminos manuales de invocación.

## Consecuencia

“Jarvis” no activa el asistente mientras Modo de bajo consumo esté habilitado. `⌃⇧Espacio`, Menu Bar
y HUD continúan disponibles; al desactivar el modo, la escucha vuelve tras estabilidad acústica.
