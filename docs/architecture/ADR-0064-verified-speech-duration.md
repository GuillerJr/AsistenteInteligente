# ADR-0064: Duración de voz verificada

- Estado: aceptado
- Fase: 3 — Capacidades sensoriales
- Fecha: 2026-08-21

## Contexto

El detector Swift calcula `duration_milliseconds` desde sus timestamps monotónicos, pero el daemon
solo comprobaba que un evento `ended` incluyera algún valor dentro del rango. Una duración
inconsistente podía alcanzar el HUD y los consumidores de turnos.

## Decisión

Un final de voz debe cumplir exactamente:

`duration_milliseconds = (fin_ns - inicio_ns) // 1_000_000`

El inicio usado es el último evento `started` de la misma sesión y UUID. La comprobación ocurre antes
de almacenar la nueva muestra, incrementar el contador o cambiar el estado a `idle`.

## Filtro del algoritmo de ingeniería

1. Se contrastó el dato redundante de duración con su fuente monotónica.
2. Se descartaron tolerancia, reloj de pared y estado adicional.
3. Se reutiliza el evento de inicio ya retenido por la máquina de estados.
4. La validación añade una resta y una división enteras.
5. Toda publicación IPC hereda el rechazo `audio_speech_transition_rejected`.

## Consecuencias

La duración no puede divergir de los eventos que delimitan el turno. Un productor con una base de
tiempo nueva debe cerrar la sesión y abrir otra, igual que para cualquier regresión monotónica.
