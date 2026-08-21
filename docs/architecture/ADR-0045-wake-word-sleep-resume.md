# ADR-0045: Suspensión del wake word durante el reposo

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** el reintento ante fallos de audio no representa el ciclo normal de reposo de
   macOS y puede agotarse antes de que el hardware vuelva a estar disponible.
2. **Eliminar:** no se añade polling, temporizador permanente, dependencia ni segundo motor de audio.
3. **Simplificar:** se consumen únicamente `willSleepNotification` y `didWakeNotification` de
   `NSWorkspace`.
4. **Acelerar:** dormir detiene el detector inmediatamente; despertar reutiliza la espera acústica y
   el arranque existentes.
5. **Automatizar:** la transición sigue el ciclo del sistema sin intervención ni ventana del usuario.

## Decisión

La Menu Bar registra una sola pareja de observadores durante su tarea principal. Antes del reposo
cancela reanudaciones y recuperaciones pendientes, detiene `AVAudioEngine`, restablece el único
reintento y publica `paused`. Al despertar descarta cualquier motor residual y programa la ruta de
reanudación existente.

La reanudación exige que el usuario conserve el opt-in y que el modelo siga validado. También espera
750 ms de quietud mediante `WakeWordResumeGate`; no solicita permisos, no abre interfaz y no inicia
captura de transcripción.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** alinea el micrófono con el ciclo real del hardware macOS.
- **Fase 5 — Voice-first:** conserva la activación de fondo después del reposo sin trabajo manual.

## Consecuencia

El reposo normal deja de consumir el presupuesto destinado a una avería real del stream. Si el
reinicio posterior falla, continúa aplicándose la recuperación acotada y fail-closed existente.
