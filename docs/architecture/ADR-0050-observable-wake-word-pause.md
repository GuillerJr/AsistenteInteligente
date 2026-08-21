# ADR-0050: Causa observable de pausa del wake word

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** un único estado `paused` dejó de ser accionable al incorporar controles de audio,
   sistema, runtime, temperatura y energía.
2. **Eliminar:** no se añade panel, historial, alerta, preferencia ni endpoint IPC.
3. **Simplificar:** el modelo conserva un enum efímero y la línea existente del menú lo representa.
4. **Acelerar:** cada transición asigna la causa junto con el estado, sin inferencias posteriores.
5. **Automatizar:** la causa cambia a `resuming` durante la compuerta acústica y se borra al arrancar.

## Decisión

`WakeWordPauseReason` admite únicamente `audio`, `system`, `runtime`, `thermal`, `lowPower` y
`resuming`. La presentación usa textos fijos en español. Integridad comprometida y daemon caído se
colapsan como “servicio no disponible”, porque las filas de seguridad existentes conservan el
diagnóstico autorizado.

La causa vive solo en `MenuBarModel`; no se persiste, registra, transmite ni incorpora al HUD. Los
estados `off`, `unavailable`, `starting`, `listening`, `recovering` y `failed` la limpian. Una pausa
por captura o síntesis conserva “audio en uso” hasta que comienza realmente el detector.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** vuelve comprensible la disponibilidad real del micrófono.
- **Fase 4 — Ciberseguridad:** evita exponer detalles internos al describir un bloqueo de integridad.
- **Fase 5 — Voice-first:** explica la falta de activación dentro de la superficie mínima existente.

## Consecuencia

El usuario puede distinguir una pausa normal de una condición energética o de servicio sin abrir
otra interfaz. La causa no altera la política, los reintentos ni el consumo en segundo plano.
