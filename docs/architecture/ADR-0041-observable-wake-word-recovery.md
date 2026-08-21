# ADR-0041: Recuperación observable del wake word

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** mostrar `falló` durante una recuperación programada contradice el estado real.
2. **Eliminar:** no se añade progreso, cuenta regresiva, alerta ni telemetría.
3. **Simplificar:** un único caso `recovering` representa la espera acotada existente.
4. **Acelerar:** la Menu Bar reutiliza su etiqueta y símbolo de espera.
5. **Automatizar:** el estado cambia a `starting` cuando vence el plazo y a `failed` solo si se agota
   el cupo.

## Decisión

`WakeWordListeningState` incorpora `recovering`. Al consumir el único reintento, el coordinador
publica ese estado antes de esperar dos segundos. Notificaciones repetidas durante la misma espera
no reemplazan el estado. Cuando empieza el nuevo `AVAudioEngine`, el flujo existente publica
`starting` y posteriormente `listening` o `failed`.

La Menu Bar presenta `Escucha “Jarvis”: recuperando` con un reloj. La acción para desactivar sigue
disponible; el reintento manual solo aparece cuando la recuperación realmente terminó en fallo.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** refleja con precisión el ciclo del micrófono.
- **Fase 5 — Voice-first:** comunica una transición de fondo sin abrir interfaz adicional.

## Consecuencia

El usuario distingue una interrupción transitoria de un fallo persistente. No cambia la política de
reintentos, los permisos, el modelo ni la cantidad de audio procesado.
