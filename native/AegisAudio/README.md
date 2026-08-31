# AegisAudio

Frontera nativa de micrófono y DSP para Apple Silicon. No descarga modelos y no emite audio crudo.
Desde la raíz del repositorio, la verificación reproducible es:

```bash
./script/test_native.sh
./script/build_and_run.sh --verify
```

El primer comando fija el SDK y Swift Testing del Command Line Toolchain; el segundo construye,
firma, empaqueta, lanza y verifica `Jarvis`.

El producto SwiftPM `jarvis-local-embedding` usa `NaturalLanguage.NLEmbedding` para convertir lotes
acotados de texto español en vectores normalizados sin red. `build_and_run.sh` lo incluye en
`Jarvis.app/Contents/Helpers`; el daemon verifica propietario, permisos y contrato JSON antes de
usarlo. Su ausencia o un error degradan cada recuperación a SQLite/FTS5.

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
activa captura automáticamente. El mismo sondeo usa `runtime.preflight`; una auditoría
comprometida o no verificable bloquea `Hablar` y enciende el escudo de seguridad. Solo los
cambios de estado agregados llegan a Unified Logging. `Hablar` exige daemon, integridad y ambos
permisos, ejecuta Apple Speech on-device y termina tras 1,2 segundos de silencio. Espera hasta ocho
segundos para el inicio de voz, tiene un límite total de 60 segundos, descarta todos los eventos
parciales y envía únicamente el transcript final por el UDS
autenticado. La app espera el job por un máximo de 60 segundos y pronuncia localmente una respuesta
acotada. La ruta principal solicita NVIDIA Magpie al daemon, que conserva la API key y transmite
PCM mono de 22,05 kHz sin escribir WAV ni archivos temporales. Swift valida HMAC, secuencia, formato
y bloques de hasta 4 KiB; una cola circular con 16 posiciones pendientes y 8 programadas inicia
AVAudioEngine dentro de 150 ms desde el primer bloque. Si Magpie no inicia en 1,8 segundos, usa una
voz española estándar mejorada,
sin voces de personaje ni reducción artificial de tono, mediante `AVSpeechSynthesizer`. Ninguna ruta
registra texto, token o digest. El script produce
`dist/Jarvis.zip`; puede usar una identidad real mediante `AEGIS_CODESIGN_IDENTITY`, y usa
firma ad hoc cuando no existe una instalada.

Al terminar una respuesta, el sensor acústico conserva una ventana monotónica de seguimiento de
cinco segundos. Tres buffers consecutivos sobre el umbral adaptativo abren Apple Speech on-device
sin repetir el wake word; el silencio devuelve el sistema al modo «Jarvis». Una interrupción durante
Magpie programa una rampa Core Audio exacta de 150 ms y cancela simultáneamente el job autenticado.

La transcripción final también reconoce localmente órdenes exactas para iniciar o cancelar un único
temporizador de entre un segundo y 24 horas. La app lo implementa con una tarea Swift cancelable,
sin bloquear un hilo ni añadir proceso, IPC, persistencia, red o dependencia. Al vencer emite un beep
y solo habla si no reemplaza otro estado de voz. Una consulta exacta informa el tiempo restante desde
el deadline monotónico, sin sondeo. Pausar cancela la espera conservando el remanente y reanudar
programa ese mismo remanente; el temporizador desaparece al terminar la app.
Las cantidades pueden llegar como cifras o palabras del uno al sesenta en español o inglés. Una
tabla inmutable generada con `NumberFormatter` evita depender del formato elegido por Apple Speech,
sin añadir NLP o inferencia.

Una pregunta de voz exacta sobre la aplicación activa usa `NSWorkspace.frontmostApplication` y
responde localmente después del preflight normal del turno. Solo se pronuncia el nombre acotado; el
transcript no se envía a `voice.submit` y no se captura pantalla ni se registran el nombre, bundle
identifier, ventanas o contenido.

Las preguntas exactas «¿Quién soy?» y «¿Me reconoces?» reutilizan el `speakerID` ya validado por el
clasificador local en ese turno. La app no ejecuta otro análisis, no expone el score y no envía el
transcript mediante `voice.submit`. Una coincidencia solo personaliza la respuesta: nunca autentica,
autoriza herramientas o sustituye una aprobación.

La pregunta exacta «¿Qué puedes hacer?» produce una guía local acotada. La respuesta comprueba el
estado ya disponible de Pantalla y Control antes de mencionar control visual, no llama a un modelo
ni envía el transcript mediante `voice.submit`. Describir una capacidad nunca la autoriza.

Para que macOS conserve los permisos de pantalla y control entre actualizaciones locales, crea una
sola vez la identidad estable de desarrollo en el llavero del usuario. Su clave privada es local, no
exportable y queda autorizada únicamente para `/usr/bin/codesign`:

```bash
./script/local_codesign_identity.sh install
./script/local_codesign_identity.sh status
```

`build_and_run.sh` detecta automáticamente `Jarvis Local Development`. Una identidad indicada de
forma explícita mediante `AEGIS_CODESIGN_IDENTITY` continúa teniendo prioridad.
Después de la primera instalación firmada, `./script/menu_bar_service.sh computer-permissions`
reinicia Jarvis y solicita únicamente pantalla y control mediante los paneles TCC de macOS.

Para QA visual del notch sin capturar audio ni contactar al daemon, una compilación DEBUG puede
abrir un estado fijo con `./script/build_and_run.sh --notch-preview listening` o recorrer todos con
`./script/build_and_run.sh --notch-preview cycle`. El estado `approval` añade una confirmación local
inerte y verifica la apertura automática de la ventana; no puede ejecutar una herramienta. El código
de galería se excluye de Release.

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

El bundle contiene además `JarvisComputerHelper.app`, un helper LSUIElement ARM64 con firma anidada.
La tarjeta `CONTROL` es la única vía que solicita Screen Recording y Accessibility; el arranque
normal solo consulta el estado. La app Jarvis espera por el IPC HMAC existente una única orden del
daemon y entonces ejecuta el helper, evitando que macOS atribuya Screen Recording a Homebrew Python.
Cada invocación recibe por stdin un JSON estricto de hasta 8 KiB, no acepta shell ni argumentos de
acción, y responde con estado acotado. Captura con ScreenCaptureKit, excluye Jarvis y el propio
helper, reduce a un JPEG en memoria y nunca escribe la imagen en disco.

Antes de hacer clic o escribir, el helper verifica que el bundle esperado siga al frente y que el
elemento Accessibility pertenezca a ese proceso. Rechaza campos seguros, etiquetas sensibles,
aplicaciones restringidas y atajos fuera de la lista de navegación. Un `NSPanel` transparente,
click-through y no activable dibuja el retículo propio de Jarvis; el helper usa `AXPress` y nunca
mueve el cursor nativo. Clic derecho, doble clic, arrastre y fallback al mouse del usuario permanecen
bloqueados. La Menu Bar conserva el job ID del control aprobado y muestra `DETENER CONTROL` hasta
que finalice o sea cancelado.

Al arrancar registra `⌃⇧Espacio` mediante Carbon para iniciar explícitamente `startVoiceTurn()` desde
cualquier aplicación. No instala un monitor de eventos ni solicita Accesibilidad/Input Monitoring;
si el atajo está ocupado, la Menu Bar informa “Atajo: no disponible” y el resto continúa operativo.

La activación por “Jarvis” exige un clasificador SoundAnalysis/Core ML compilado dentro del
bundle como `JarvisWakeWord.mlmodelc`. El host valida que no sea un enlace, que incluya la etiqueta
exacta `jarvis` y que tenga como máximo 16 clases. Ausencia o invalidez se muestran en la Menu Bar y
mantienen la función apagada y no abren el micrófono; nunca se usa transcripción como fallback.

Con un modelo válido, la Menu Bar muestra una acción separada para habilitar la escucha. El stream
usa `AVAudioEngine` y `SNAudioStreamAnalyzer` sin persistencia ni red. Dos ventanas de fondo arman
la compuerta; después, dos ventanas consecutivas con `jarvis` como primera clasificación y confianza
mínima de 0,85 activan el turno existente. Este armado evita disparos al iniciar sobre ruido
clasificado erróneamente como palabra clave; un enfriamiento de cinco segundos evita repeticiones.
La escucha se pausa durante transcripción,
enrolamiento y salida hablada. Solo se reanuda tras 750 ms continuos sin actividad acústica local;
si una captura o síntesis reaparece, el intervalo comienza otra vez.
Un cambio de dispositivo de entrada detiene explícitamente el engine y programa un único reintento
tras dos segundos. El cupo se restablece solo después de 30 segundos de escucha estable, evitando
bucles ante fallos persistentes de hardware o permisos. Si el único reintento falla, el menú separa
`Reintentar escucha “Jarvis”` de la acción para desactivarla. Durante el intento acotado informa
`recuperando` en lugar de declarar un fallo prematuro.

El producto SwiftPM `jarvis-wake-word-trainer` entrena localmente ese activo mediante Create ML. El
dataset debe vivir fuera del repositorio y contener exactamente `jarvis/` y `background/`, con 20 a
500 clips WAV, AIFF o CAF por clase. Cada clip debe durar entre 0,4 y 3 segundos; el dataset completo
se limita a 512 MiB. El entrenamiento usa Audio Feature Print, validación determinista y rechaza un
error superior al 25 %. `script/train_wake_word.sh` guarda el modelo compilado bajo Application
Support, nunca sobrescribe uno existente y no persiste ni mueve los audios de entrada.

`Preparar activación por “Jarvis”…` abre una ventana nativa bajo demanda. Cada clic captura un solo
CAF de dos segundos en `~/Library/Application Support/Aegis/WakeWordEnrollment`, separado como
`jarvis` o `background`. La app exige permiso de micrófono previo, muestra el progreso mínimo de
20+20, limita cada clase a 100 y protege directorios con `0700` y muestras con `0600`. La guía rota
voz, distancia, ruido y palabras parecidas según el conteo aceptado, sin guardar metadatos
adicionales. Cada acción espera un segundo y solo abre el micrófono cuando la UI cambia a
`Grabando`. No graba al
abrir la ventana ni ejecuta entrenamiento. El DSP local descarta saturación en ambas clases y exige
al menos 120 ms audibles para `jarvis`; `background` conserva silencio válido. Al completar el
mínimo, `./script/train_wake_word.sh` usa ese dataset por defecto; todavía se puede pasar una ruta
explícita como único argumento. Al llegar a 20+20, la misma ventana muestra y permite copiar
`./script/activate_wake_word.sh`; nunca ejecuta shell ni modifica el portapapeles sin un clic.

La acción confirmada `Eliminar muestras…` valida primero todo el dataset, borra solo los CAF
reconocidos y conserva tanto los directorios privados como cualquier modelo ya entrenado.

`./script/activate_wake_word.sh` valida el dataset, entrena solo si falta el modelo, reinstala el
bundle firmado y confirma el modelo dentro de la app. Si la instalación se interrumpe, una nueva
ejecución valida el modelo existente y reanuda sin sobrescribirlo.

La ventana `Preparar identidad de voz…` recolecta de forma explícita 20 clips por persona y 20 de
fondo. Al completar al menos un perfil, `Entrenar modelo local` lanza en segundo plano el producto
`jarvis-speaker-trainer` empaquetado en `Contents/Helpers`. El helper se firma antes que el bundle y
solo recibe las rutas fijas del dataset y del modelo privado: no ejecuta shell ni acepta parámetros
desde la interfaz. La app pausa la escucha residente y bloquea otros propietarios del micrófono
durante Create ML.

El modelo resultante permanece en
`~/Library/Application Support/Aegis/Models/JarvisSpeakerIdentity.mlmodelc`, dentro de un directorio
`0700` propiedad del usuario. Cada transcriptor nuevo lo resuelve directamente desde Application
Support, de modo que queda activo sin reinstalar Jarvis. Un activo ausente, enlazado, inválido o bajo
permisos inseguros falla cerrado; el modelo nunca se sobrescribe automáticamente.

El cliente nativo valida `swarm.activity` y su actualización versionada `swarm.wait`: admite como
máximo los siete roles conocidos, un contador entre 1 y 128 por rol y rechaza duplicados. Un único
monitor autenticado espera cambios hasta 20 segundos y alimenta HUD y notch sin sondeo a 4 Hz.

Un job que requiera autorización detiene la voz y enciende el estado de escudo. La Menu Bar ofrece
“Revisar aprobación…”. Una transición nueva abre automáticamente esa misma ventana singleton; el
menú permite recuperarla si fue cerrada. Muestra resumen exacto, advertencia específica, caducidad y
acciones “Denegar”/“Aprobar una vez”. Cerrar no cambia el job. El cliente valida el digest antes de
llamar `jobs.approve` y nunca registra digest, destino ni puertos.

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
