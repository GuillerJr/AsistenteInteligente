# AegisAudio

Frontera nativa de micrófono y DSP para Apple Silicon. No descarga modelos y no emite audio crudo.

```bash
swift build
swift test
.build/debug/aegis-audio-helper permission
.build/debug/aegis-audio-helper meter --duration-seconds 5 --interval-ms 50
.build/debug/aegis-audio-helper speech-status --locale es-US
.build/debug/aegis-audio-helper transcribe --duration-seconds 10 --locale es-US
```

`permission` solo consulta TCC. `meter` funciona únicamente si el proceso anfitrión ya tiene acceso
al micrófono y termina tras un máximo de 60 segundos. El ejecutable SwiftPM no solicita permisos:
esa responsabilidad corresponderá a la futura aplicación Menu Bar firmada y con `Info.plist`.

`speech-status` consulta soporte, permisos y disponibilidad on-device sin solicitarlos. `transcribe`
es push-to-talk acotado, exige permisos previos y configura Apple Speech para impedir reconocimiento
remoto. No instala assets de idioma. El locale predeterminado es `es-US`, disponible como español
latinoamericano en este host; puede cambiarse con `--locale`.

Cada línea `audio.meter` contiene RMS, pico, dBFS, actividad, clipping y metadatos temporales. Un VAD
con histéresis añade opcionalmente un evento `speech_event` de inicio o fin de turno. No contiene
PCM, texto transcrito ni una ruta de archivo. La envoltura está diseñada para que el host añada el
`session_id` y publique la muestra mediante el UDS autenticado de Aegis.

Los eventos `speech.transcript` parciales sirven para feedback local. Solo el evento final debe
enviarse a `voice.submit`; contiene texto normalizado y acotado, nunca audio.
