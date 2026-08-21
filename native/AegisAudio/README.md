# AegisAudio

Frontera nativa de micrófono y DSP para Apple Silicon. No descarga modelos y no emite audio crudo.
Desde la raíz del repositorio, la verificación reproducible es:

```bash
./script/test_native.sh
./script/build_and_run.sh --verify
```

El primer comando fija el SDK y Swift Testing del Command Line Toolchain; el segundo construye,
firma, empaqueta, lanza y verifica `Jarvis`.

`permission` solo consulta TCC. `meter` funciona únicamente si el proceso anfitrión ya tiene acceso
al micrófono y termina tras un máximo de 60 segundos. El ejecutable SwiftPM no solicita permisos:
esa responsabilidad pertenece a `Jarvis`, que solo solicita TCC mediante botones explícitos.

`speech-status` consulta soporte, permisos y disponibilidad on-device sin solicitarlos. `transcribe`
es push-to-talk acotado, exige permisos previos y configura Apple Speech para impedir reconocimiento
remoto. No instala assets de idioma. El locale predeterminado es `es-US`, disponible como español
latinoamericano en este host; puede cambiarse con `--locale`.

Cada línea `audio.meter` contiene RMS, pico, dBFS, actividad, clipping y metadatos temporales. Un VAD
con histéresis añade opcionalmente un evento `speech_event` de inicio o fin de turno. No contiene
PCM, texto transcrito ni una ruta de archivo. La envoltura está diseñada para que el host añada el
`session_id` y publique la muestra mediante el UDS autenticado de Jarvis.

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

`Jarvis` es una app nativa `LSUIElement`: muestra estado del daemon, estado TCC y acciones para
solicitar o abrir los ajustes de micrófono/Speech. Consulta el daemon cada diez segundos, pero nunca
activa captura automáticamente. El mismo sondeo verifica `security.status`; una auditoría
comprometida o no verificable bloquea `Hablar` y enciende el escudo de seguridad. Solo los
cambios de estado agregados llegan a Unified Logging. `Hablar` exige daemon, integridad y ambos
permisos, ejecuta Apple Speech on-device y termina tras 1,2 segundos de silencio. Espera hasta ocho
segundos para el inicio de voz, tiene un límite total de 60 segundos, descarta todos los eventos
parciales y envía únicamente el transcript final por el UDS
autenticado. La app espera el job por un máximo de 60 segundos y pronuncia localmente una respuesta
acotada con `AVSpeechSynthesizer`; no persiste el resultado. El script produce
`dist/Jarvis.zip`; puede usar una identidad real mediante `AEGIS_CODESIGN_IDENTITY`, y usa
firma ad hoc cuando no existe una instalada.

“Preguntar sobre imagen…” abre un único `NSOpenPanel` solo por acción explícita. `ImageIO`
inspecciona el archivo sin conservarlo, rechaza fuentes no regulares, mayores a 20 MB o 100
megapíxeles y genera localmente un JPEG de hasta 32 KiB. Después captura hasta detectar silencio una
instrucción con Apple Speech estrictamente on-device y envía únicamente el texto final junto al
adjunto mediante `image.submit`. Reutiliza la conversación activa y elimina el acceso al archivo al
terminar; no guarda rutas, miniaturas, Base64 ni audio.

“Preguntar sobre pantalla” exige un clic separado y permiso TCC de Screen Recording. ScreenCaptureKit
captura una sola imagen SDR del display principal, excluye el PID de Jarvis, el cursor y cualquier
audio, y la reduce localmente a JPEG de hasta 32 KiB antes de abrir el micrófono para la pregunta.
Si Jarvis no puede excluirse o el permiso no está activo, falla sin capturar. La imagen permanece solo
en memoria hasta `image.submit` y nunca se guarda en disco.

Al arrancar registra `⌃⇧Espacio` mediante Carbon para iniciar explícitamente `startVoiceTurn()` desde
cualquier aplicación. No instala un monitor de eventos ni solicita Accesibilidad/Input Monitoring;
si el atajo está ocupado, la Menu Bar informa “Atajo: no disponible” y el resto continúa operativo.

El cliente nativo valida además `swarm.activity`: admite como máximo los siete roles conocidos, un
contador entre 1 y 128 por rol y rechaza duplicados. El HUD lo consulta únicamente mientras está
visible y enciende el clúster espacial del agente activo.

Un job que requiera autorización detiene la voz y enciende el estado de escudo. La Menu Bar ofrece
“Revisar aprobación…”, que abre bajo demanda una única ventana con el resumen exacto, caducidad y
acciones “Denegar”/“Aprobar una vez”. El cliente valida el digest antes de llamar `jobs.approve` y
nunca registra digest, destino ni puertos.

Para operación persistente, `./script/menu_bar_service.sh install` desde la raíz instala el bundle
en `~/Applications` y registra su apertura al iniciar sesión. `uninstall` desactiva ese autoinicio y
cierra el proceso, sin borrar el bundle.

“Mostrar HUD…” abre bajo demanda una ventana singleton transparente de 560 puntos. SceneKit genera
210 nodos con distribución Fibonacci y siete regiones espaciales; cada región comparte un material
que se ilumina según `swarm.activity`. Durante el push-to-talk, el `Float` normalizado que ya calcula
Accelerate expande la esfera hasta 14%; al terminar vuelve a cero. Ese nivel no se registra ni
persiste y ningún PCM llega a la UI. La ventana no es restaurable, incluye cierre propio y puede
moverse por el fondo. No existe Tauri/Electron ni runtime JavaScript: el render nativo reduce memoria
y dependencias en Apple Silicon, mientras conserva la separación entre estado y presentación.
