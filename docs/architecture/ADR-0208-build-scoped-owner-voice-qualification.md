# ADR-0208: calificación de voz física aislada por compilación

- Estado: aceptada
- Responsable: Guillermo (`gzambrano27`)
- Bloque: P10

## Contexto

La disponibilidad de micrófono, Apple Speech y modelos Core ML solo demuestra que los componentes
existen. Tampoco basta con clasificar grabaciones de enrolamiento: esa ruta no prueba el wake word
en vivo, la conversación, la ventana de seguimiento ni la interrupción del audio que está sonando.
Antes de P10, `runtime-evidence.json` contaba trabajos vocales terminados, pero no podía atribuir esas
otras transiciones críticas.

## Decisión

El snapshot operacional evoluciona a esquema `2.0` y añade cuatro contadores monotónicos:
`wake_word_detections`, `follow_up_voice_turns`, `successful_interruptions` y
`completed_playbacks`. Cada contador se incrementa exactamente en la frontera donde el componente
nativo ha completado su parte; no se deriva de intención, texto ni una simulación Python.

La identidad completa del commit sigue siendo la frontera de causalidad. Un cambio de esquema o de
revisión reinicia el snapshot mediante escritura atómica privada. La migración no interpreta
evidencia antigua como prueba P10.

La compuerta combina esos contadores con tres fuentes independientes:

1. readiness nativo y estado energético del daemon autenticado por HMAC;
2. métricas de trabajos vocales de la revisión activa;
3. salida efímera del calibrador Core ML sobre enrolamiento real y distractores TTS locales.

P10 exige al menos 90% de reconocimiento del propietario, primer parcial ≤2 s y vuelta activa ≤8 s.
La calibración requiere separación estricta de clases; una voz negativa nunca puede igualar el
umbral. El archivo temporal de calibración se crea con `umask 077` y se destruye al salir.

## Consecuencias

La voz deja de ser una afirmación basada en componentes instalados y pasa a tener una prueba física
repetible. La prueba necesita cooperación humana por diseño: sintetizar la voz del propietario o
editar los contadores violaría la propiedad que se intenta certificar. Low Power Mode y presión
térmica bloquean el flujo sin cambiar silenciosamente la configuración del Mac.
