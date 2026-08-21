# ADR-0048: Wake word consciente de presión térmica

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** un clasificador continuo no debe agravar presión térmica en un MacBook Air sin
   ventilador solo para conservar disponibilidad momentánea.
2. **Eliminar:** no se leen sensores privados, no se estima temperatura y no se añade polling.
3. **Simplificar:** macOS ya clasifica la presión en `ProcessInfo.thermalState` y publica sus cambios.
4. **Acelerar:** la transición reutiliza `WakeWordAvailabilityPolicy` y el mismo start/stop del audio.
5. **Automatizar:** Jarvis pausa en `serious`/`critical` y reanuda en `nominal`/`fair`.

## Decisión

`WakeWordThermalPolicy` traduce el estado nativo a un booleano: solo `nominal` y `fair` permiten
escucha. Estados futuros desconocidos fallan cerrados. La Menu Bar registra una vez
`ProcessInfo.thermalStateDidChangeNotification`; no crea temporizadores ni trabajo periódico.

La presión elevada cancela reanudaciones y recuperaciones, detiene SoundAnalysis y publica `paused`
sin borrar el opt-in. La recuperación térmica solo inicia desde `paused` o `unavailable`, y continúa
exigiendo modelo válido, Micrófono autorizado y runtime íntegro. `failed` y `recovering` permanecen
fuera de esta reanudación. El arranque reutiliza los 750 ms de quietud acústica para no solaparse con
voz sintetizada, captura o enrolamiento.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** adapta la captura continua al límite térmico del host.
- **Fase 5 — Voice-first:** conserva operación automática sin sacrificar estabilidad del equipo.

## Consecuencia

Durante presión seria o crítica la frase “Jarvis” no activa el asistente; el atajo y las acciones
explícitas permanecen disponibles. La escucha vuelve automáticamente cuando macOS informa presión
aceptable.
