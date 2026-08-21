# ADR-0063: Telemetría de audio estrictamente monotónica

- Estado: aceptado
- Fase: 3 — Capacidades sensoriales
- Fecha: 2026-08-21

## Contexto

El contrato exige secuencia y tiempo monotónicos, pero el manager solo comparaba `sequence`. Una
muestra con secuencia nueva y timestamp anterior podía reemplazar la medición vigente y alterar la
base temporal usada por eventos de voz.

## Decisión

Después de la primera muestra, tanto `sequence` como `monotonic_nanoseconds` deben aumentar de forma
estricta. La comprobación ocurre antes de actualizar muestra, contador, lease o transición de voz.
El error IPC existente continúa siendo `audio_sequence_rejected`.

## Filtro del algoritmo de ingeniería

1. Se contrastó el requisito declarado con la condición realmente implementada.
2. Se descartaron tolerancias, reordenamiento y buffer de muestras atrasadas.
3. Se añade una comparación entera al guard existente.
4. El camino válido no añade I/O ni asignaciones.
5. Todas las publicaciones heredan la regla desde el manager central.

## Consecuencias

Muestras repetidas, reordenadas o con regresión temporal fallan atómicamente. Reiniciar una fuente
de tiempo requiere cerrar la sesión actual y abrir una identidad de sesión nueva.
