# ADR-0029: Modelo de activación fail-closed

- Estado: aceptado
- Fecha: 2026-08-20

## Filtro de ingeniería

1. **Cuestionar:** escuchar mediante transcripción general no equivale a un detector de palabra ligero.
2. **Eliminar:** no se incorpora un SDK externo, modelo descargado o fallback remoto.
3. **Simplificar:** SoundAnalysis consume un único clasificador Core ML firmado dentro del bundle.
4. **Acelerar:** primero se fija y prueba el contrato del activo; después se conecta la captura.
5. **Automatizar:** empaquetado y arranque rechazan el modelo ausente o inválido sin abrir micrófono.

## Decisión

El activo opcional se llama `JarvisWakeWord.mlmodelc`. Debe ser un directorio compilado, no un
enlace simbólico, aceptado por `SNClassifySoundRequest`, incluir la etiqueta exacta `jarvis` y no
exponer más de 16 clasificaciones. Core ML lo carga con CPU y Neural Engine; no se admite red.

Si el activo falta, la Menu Bar informa `modelo pendiente`. Si existe pero no satisface el contrato,
informa `modelo inválido`. Solo un modelo válido llega al estado `disponible, apagada`; este corte no
abre el micrófono ni ofrece todavía un interruptor de escucha.

## Consecuencias

Jarvis no simula un *wake word* mediante reconocimiento continuo ni degrada silenciosamente a una
ruta más costosa. El siguiente corte podrá conectar el stream y el consentimiento explícito sobre
una frontera ya validada. El modelo deberá obtenerse o entrenarse mediante un proceso separado y
auditable antes de distribuirlo.
