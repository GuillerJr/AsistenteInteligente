# AegisAudio

Frontera nativa de micrófono y DSP para Apple Silicon. No descarga modelos y no emite audio crudo.

```bash
swift build
swift test
.build/debug/aegis-audio-helper permission
.build/debug/aegis-audio-helper meter --duration-seconds 5 --interval-ms 50
```

`permission` solo consulta TCC. `meter` funciona únicamente si el proceso anfitrión ya tiene acceso
al micrófono y termina tras un máximo de 60 segundos. El ejecutable SwiftPM no solicita permisos:
esa responsabilidad corresponderá a la futura aplicación Menu Bar firmada y con `Info.plist`.

Cada línea `audio.meter` contiene RMS, pico, dBFS, actividad, clipping y metadatos temporales. No
contiene PCM, texto transcrito ni una ruta de archivo. La envoltura está diseñada para que el host
añada el `session_id` y publique la muestra mediante el UDS autenticado de Aegis.
