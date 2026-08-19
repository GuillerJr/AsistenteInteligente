# AegisAudio

Frontera nativa de micrófono y DSP para Apple Silicon. No descarga modelos y no emite audio crudo.

```bash
swift build
swift test
.build/debug/aegis-audio-helper permission
.build/debug/aegis-audio-helper meter --duration-seconds 5 --interval-ms 50
.build/debug/aegis-audio-helper speech-status --locale es-US
.build/debug/aegis-audio-helper transcribe --duration-seconds 10 --locale es-US
.build/debug/aegis-audio-helper ipc-health
.build/debug/aegis-audio-helper transcribe-submit --duration-seconds 10 --locale es-US
```

`permission` solo consulta TCC. `meter` funciona únicamente si el proceso anfitrión ya tiene acceso
al micrófono y termina tras un máximo de 60 segundos. El ejecutable SwiftPM no solicita permisos:
esa responsabilidad pertenece a `AegisMenuBar`, que solo solicita TCC mediante botones explícitos.

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

`ipc-health` verifica el daemon mediante el protocolo local `1.0`. `transcribe-submit` realiza esa
verificación antes de abrir el micrófono y, tras una transcripción final, envía `voice.submit`. El
cliente valida propietario y permisos `0600` del socket, aplica un frame máximo de 64 KiB, firma
solicitud y respuesta con HMAC-SHA256 y rechaza timestamps obsoletos o respuestas no vinculadas.

El secreto se lee desde macOS Keychain (`ai.aegis.ipc-auth`/`default`) mediante `/usr/bin/security`
con timeout y pipes privados; no se admite como argumento o variable de entorno. Esta vía evita los
prompts de ACL que Security.framework produce para un ejecutable SwiftPM sin firma. La futura app
con identidad de distribución podrá usar Security.framework directamente.

Para integrar productores separados, `submit-transcript` acepta por stdin un único JSON final de
hasta 16 KiB. Rechaza campos adicionales, eventos remotos o parciales y nunca acepta audio crudo.

## Menu Bar App

Desde la raíz del repositorio:

```bash
./script/build_and_run.sh --verify
```

`AegisMenuBar` es una app nativa `LSUIElement`: muestra estado del daemon, estado TCC y acciones para
solicitar o abrir los ajustes de micrófono/Speech. Consulta el daemon cada diez segundos, pero nunca
activa captura automáticamente. `Hablar 8 s` exige daemon y ambos permisos, ejecuta Apple Speech
on-device, descarta todos los eventos parciales y envía únicamente el transcript final por el UDS
autenticado. La app espera el job por un máximo de 60 segundos y pronuncia localmente una respuesta
acotada con `AVSpeechSynthesizer`; no persiste el resultado. El script produce
`dist/AegisMenuBar.zip`; puede usar una identidad real mediante `AEGIS_CODESIGN_IDENTITY`, y usa
firma ad hoc cuando no existe una instalada.

Un job que requiera autorización detiene la voz y enciende el estado de escudo. La Menu Bar ofrece
“Revisar aprobación…”, que abre bajo demanda una única ventana con el resumen exacto, caducidad y
acciones “Denegar”/“Aprobar una vez”. El cliente valida el digest antes de llamar `jobs.approve` y
nunca registra digest, destino ni puertos.

Para operación persistente, `./script/menu_bar_service.sh install` desde la raíz instala el bundle
en `~/Applications` y registra su apertura al iniciar sesión. `uninstall` desactiva ese autoinicio y
cierra el proceso, sin borrar el bundle.
