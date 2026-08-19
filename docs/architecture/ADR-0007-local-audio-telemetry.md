# ADR-0007: Telemetría de audio local sin retención de voz

- Estado: aceptado
- Fecha: 2026-08-18

## Contexto

Aegis necesita reaccionar visualmente a la amplitud de la voz y alimentar más adelante una tubería
de detección de actividad y transcripción. El acceso al micrófono y sus permisos TCC pertenecen al
host nativo de macOS; el daemon Python no debe adquirir permisos de captura ni recibir audio crudo
si solo necesita animar el HUD.

Una aplicación *voice-first* en segundo plano aumenta el riesgo de captura involuntaria. El primer
hito sensorial, por tanto, no puede habilitar escucha permanente, *wake word* ni grabaciones.

## Decisión

Se añade el paquete SwiftPM `native/AegisAudio`, compatible con macOS 14 o posterior y compilado de
forma nativa para Apple Silicon. Su ejecutable `aegis-audio-helper`:

- consulta el permiso del micrófono sin solicitarlo;
- solo captura tras el comando explícito `meter`, con duración entre 1 y 60 segundos;
- usa `AVAudioEngine` y Accelerate/vDSP para calcular RMS y pico;
- publica NDJSON con métricas normalizadas, nunca muestras PCM;
- no persiste audio, no abre red y no ejecuta en segundo plano por sí mismo.

El permiso TCC se solicitará más adelante desde la aplicación Menu Bar firmada, con su
`NSMicrophoneUsageDescription`. El helper CLI trata `not_determined`, `denied` y `restricted` como
estados estructurados; no intenta eludir ni modificar TCC.

El daemon expone cuatro métodos sobre el UDS autenticado existente:

- `audio.session.open`
- `audio.meter.publish`
- `audio.meter.status`
- `audio.session.close`

Solo puede existir una sesión. Cada muestra tiene esquema cerrado, secuencia creciente, valores
finitos y límites físicos. Se conserva únicamente la última muestra y un contador; el estado se
marca obsoleto tras un segundo sin telemetría. El payload rechaza campos adicionales, incluido PCM.

## Consecuencias

El HUD podrá consumir amplitud y actividad sin que Python pueda reconstruir la conversación. La
transcripción y el *wake word* requieren una decisión posterior, un flujo separado y consentimiento
explícito. La aplicación Menu Bar también deberá autenticar cada publicación con el secreto IPC de
Keychain y cerrar la sesión al abandonar la captura.

La integración posterior del HUD, documentada en ADR-0024, elimina incluso esa publicación para la
animación: reutiliza `activity` dentro del proceso nativo y la descarta al terminar el push-to-talk.
