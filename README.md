# Jarvis

Núcleo local, auditable y *voice-first* para un asistente táctico multiagente en macOS Apple Silicon.

Jarvis es el nombre visible de la aplicación. El namespace técnico heredado `aegis` se conserva en
el paquete Python, variables de entorno, servicios de Keychain, identificadores de bundle y rutas de
datos para mantener credenciales, permisos TCC, memoria y LaunchAgents existentes.

![Logo de Jarvis](native/AegisAudio/AppBundle/Resources/JarvisLogo.png)

La identidad visual usa una esfera de nodos con una `J` implícita, alineada con los clústeres del
HUD. El master PNG de 1024 px y el recurso macOS `Jarvis.icns` viven junto al `Info.plist`. La Menu
Bar usa en cambio el símbolo template nativo `circle.hexagongrid.fill`, sin fondo y adaptativo al
tema. Los estados permanecen dentro del menú para no comprometer la legibilidad del icono. El icono
de aplicación puede regenerarse sin dependencias externas con:

```bash
swift script/build_macos_icon.swift \
  native/AegisAudio/AppBundle/Resources/JarvisLogo.png \
  native/AegisAudio/AppBundle/Resources/Jarvis.icns
```

Esta primera vertical contiene:

- contratos tipados para solicitudes, rutas y respuestas;
- registro central de modelos NVIDIA NIM;
- cliente HTTP asíncrono compatible con los endpoints NIM;
- recuperación de la credencial desde macOS Keychain;
- grafo LangGraph mínimo con escalamiento por especialidad;
- Tool Broker con capacidades por agente y política de denegación por defecto;
- ejecutores locales de solo lectura, incluido sondeo TCP acotado, con auditoría JSONL encadenada;
- daemon local autenticado mediante Unix Domain Socket;
- memoria persistente local con SQLite/FTS5 y aislamiento por namespace;
- telemetría de amplitud local con Swift/Accelerate y sin retención de PCM;
- cliente Swift del UDS con autenticación mutua y credencial IPC en Keychain;
- pruebas sin llamadas reales a servicios externos.

## Roadmap activo

| Fase | Estado | Corte actual |
| --- | --- | --- |
| 1. Orquestación | operativa | LangGraph, jobs y LaunchAgent |
| 2. Memoria | operativa | SQLite/FTS5, RAG híbrido y conversaciones |
| 3. Sensores | operativa | voz on-device, imagen y pantalla explícitas |
| 4. Ciberseguridad | operativa | broker, aprobación, red, diagnósticos y monitor de integridad |
| 5. Interfaz | operativa | Menu Bar, atajo global, HUD 3D explícito y pulso de voz |

Las cinco fases del MVP están operativas. Distribución notarizada, actualizaciones automáticas y
acciones mutables permanecen fuera de alcance hasta definir requisitos y políticas específicas.
La activación local por la palabra “Jarvis” está implementada como opt-in, pero permanece
fail-closed hasta entrenar y empaquetar un modelo real con muestras explícitas del usuario.

## Distribución macOS

El empaquetado local usa Release y firma ad hoc. Para generar un archivo de distribución se requiere
una identidad `Developer ID Application` instalada; para notarizar se requiere además un perfil de
`notarytool` guardado previamente en Keychain:

```bash
AEGIS_CODESIGN_IDENTITY="Developer ID Application: …" ./script/release_macos.sh archive
AEGIS_CODESIGN_IDENTITY="Developer ID Application: …" \
AEGIS_NOTARY_PROFILE="aegis-notary" ./script/release_macos.sh notarize
```

La firma de distribución activa hardened runtime y timestamp de Apple. El flujo valida arquitectura
arm64, estructura, firma, ZIP, ticket grapado y Gatekeeper; no acepta secretos por argumentos ni los
guarda en el repositorio.

## Endurecimiento post-MVP

La suite somete el IPC autenticado a una ráfaga sintética de 24 trabajos con capacidad local 8. El
daemon debe aceptar exactamente ocho, rechazar el exceso con `job_capacity_reached` y completar los
aceptados después de liberar el proveedor falso. No usa NVIDIA, credenciales ni datos persistentes.

El cliente NVIDIA también se prueba con seis solicitudes sintéticas y concurrencia configurada en
dos. El transporte simulado nunca debe observar más de dos solicitudes activas.
El modelo primario y su fallback comparten un único presupuesto de timeout; agotarlo cancela el
turno y libera capacidad para el siguiente trabajo.
Un `429` definitivo abre un cooldown local compartido por chat y embeddings. Durante cinco segundos
las nuevas llamadas fallan localmente, sin consultar Keychain ni enviar tráfico adicional.

## Seguridad

La API key no debe guardarse en el repositorio ni en archivos `.env`. El servicio de Keychain usado por defecto es `ai.aegis.nvidia-nim`, con la cuenta `default`.

El cliente NVIDIA controla `model`, `messages`, `stream` y `max_tokens`. Las extensiones solo pueden
usar las cuatro opciones requeridas por routing, function calling y probes; cualquier otra se
rechaza antes de consultar Keychain o abrir red.

El descubrimiento de red solo usa conexiones TCP nativas contra IP/CIDR de loopback o redes privadas
autorizadas. Cada llamada exige confirmación exacta de un solo uso, entre uno y ocho puertos, y un
máximo de 256 direcciones. No ejecuta shell, `ping` ni `nmap`; tampoco hace DNS, fingerprinting,
envía payloads o persiste resultados. El ejecutor vuelve a validar alcance y límites aunque reciba
una autorización falsificada.

## Entorno

El proyecto requiere Python 3.11, 3.12 o 3.13 nativo `arm64`. La configuración evita depender de contenedores o binarios x86 para el núcleo local.
Los scripts Swift inspeccionan el SDK macOS activo de `/usr/bin/xcrun` y, si requiere un macro
SwiftUI ausente en el toolchain, eligen el SDK compatible más reciente del mismo directorio.
`AEGIS_MACOS_SDK` permite fijar una ruta explícita; una ruta inexistente falla antes de compilar o
crear el modelo. Build, pruebas y entrenador ejecutan el binario `swift` resuelto por el mismo
`xcrun`, nunca una coincidencia distinta tomada de `PATH`.

```bash
uv sync --all-groups --no-editable --python /opt/homebrew/bin/python3.11 --cache-dir .uv-cache
uv run --no-sync pytest
./script/aegis.sh doctor
```

`aegis doctor` verifica arquitectura, configuración y presencia de la credencial sin imprimirla.
`aegis probe-nvidia` realiza una inferencia mínima y solo informa estado y modelo, nunca el secreto.
`aegis probe-nvidia-embedding` verifica el endpoint de embeddings con una frase sintética y solo
informa modelo y dimensiones; nunca imprime el vector ni la credencial.
`aegis probe-nvidia-vision` envía un PNG sintético de 1×1 y solo informa estado y modelo; no lee
archivos ni utiliza imágenes del usuario.
`aegis probe-nvidia-tools` fuerza una llamada sintética a `security_posture`, valida nombre y
argumentos y reporta `execution=none`; nunca autoriza ni ejecuta la herramienta.
`aegis verify-audit <ruta>` comprueba permisos, secuencia y cadena hash del registro local.

## Daemon local

```bash
./script/aegis.sh daemon
./script/aegis.sh daemon-recovery
./script/aegis.sh daemon-status
./script/aegis.sh daemon-soak
```

Para operación persistente en macOS, el LaunchAgent de usuario usa el Python arm64 del proyecto,
añade únicamente `src/` a `PYTHONPATH`, arranca al iniciar sesión y reinicia el daemon si falla. El
wrapper `aegis.sh` aplica la misma ruta en terminal para impedir ejecutar una copia obsoleta de
`site-packages`:

```bash
./script/daemon_service.sh install
./script/daemon_service.sh status
```

`uninstall` retira únicamente el servicio; no borra credenciales, memoria, auditoría ni logs.

`daemon-soak` ejecuta por defecto 100 rondas autenticadas y 500 conexiones locales sin invocar
NVIDIA. Exige arquitectura arm64, integridad intacta, PID estable, p95 máximo de 250 ms y crecimiento
del pico RSS no mayor a 8 MiB. Reporta CPU consumida como proxy operativo, no como medición de
energía. Los límites se configuran con `AEGIS_SOAK_CYCLES`, `AEGIS_SOAK_MAX_P95_MS` y
`AEGIS_SOAK_MAX_RSS_GROWTH_MB`.

`daemon-recovery` es una prueba destructiva explícita y acotada del supervisor. Solo envía
`SIGTERM` al PID obtenido por IPC autenticado si LaunchAgent está cargado, la auditoría está íntegra
y no hay agentes activos. Pasa únicamente cuando aparece otro PID arm64 con integridad intacta.

El daemon escucha por defecto en `~/Library/Application Support/Aegis/aegis.sock`. El directorio y
el socket requieren permisos `0700` y `0600`, respectivamente. Cada conexión valida el UID del
proceso cliente mediante credenciales nativas de macOS; cada mensaje y respuesta se autentica además
con HMAC-SHA256 usando un secreto independiente guardado en Keychain bajo `ai.aegis.ipc-auth`.

El protocolo `1.0` limita cada frame a 64 KiB, acepta una solicitud por conexión y rechaza timestamps
fuera de ventana, nonces repetidos, métodos desconocidos y payloads inesperados. Expone `health`,
`runtime.info`, `runtime.metrics`, `swarm.submit`, `swarm.activity`, `voice.submit`, `image.submit`,
`jobs.status` y `jobs.cancel`.
`jobs.approve` consume exclusivamente la confirmación pendiente del digest exacto.
Los handlers del control plane disponen de cuatro segundos para validar y despachar cada solicitud;
un timeout cancela el handler, devuelve `handler_timeout` firmado y libera el cupo de conexión. Este
límite no acorta la ejecución asíncrona de los jobs, cuyo presupuesto permanece en 120 segundos.
`ipc_max_clients` es un cupo duro: una conexión por encima del límite se cierra antes de leer,
autenticar o encolarse. Así una ráfaga o cliente lento no crea una cola de sockets dentro del proceso.
Cada conexión admitida debe entregar el frame completo en un segundo; el presupuesto total no se
renueva con lecturas parciales y puede ajustarse con `AEGIS_IPC_READ_TIMEOUT_SECONDS`.
La escritura de la respuesta dispone de otro segundo. Un payload que exceda el frame se sustituye
por `response_too_large` firmado; un cliente que no lee no puede retener el cupo indefinidamente.

`swarm.activity` publica únicamente roles activos y cantidad de trabajos por rol. El estado es
efímero, se limpia incluso al cancelar una tarea y nunca incluye prompts, respuestas, herramientas,
`request_id`, `job_id` ni timestamps. Three.js será un consumidor visual de este contrato, no su
fuente de verdad.

`security.status` verifica fuera del event loop la cadena hash del log de auditoría y devuelve solo
`intact` o `compromised`. La Menu Bar reutiliza su sondeo de diez segundos para vigilar este estado;
un resultado ausente, inválido o comprometido bloquea nuevos turnos de voz. No se ejecutan `ps`,
`lsof` ni sondeos de red en segundo plano.

También expone `memory.put`, `memory.get`, `memory.search` y `memory.delete`. Estas operaciones pasan
por el mismo socket autenticado, validan esquemas estrictos y ejecutan el acceso SQLite fuera del
event loop.

La continuidad conversacional usa `conversations.create`, `conversations.history` y
`conversations.delete`. El UUID devuelto por `conversations.create` puede enviarse como
`conversation_id` en `swarm.submit`; `jobs.status` indica después `conversation_persisted=true` o
`false`. El namespace pertenece a la configuración del daemon y no puede elegirse desde el payload.

La telemetría sensorial usa `audio.session.open`, `audio.meter.publish`, `audio.meter.status` y
`audio.session.close`. Solo admite una sesión explícita y conserva únicamente la última medición
normalizada. Los eventos de inicio y fin de voz viajan atómicamente con su medición y se validan
como una máquina de estados; cualquier campo adicional —incluido audio PCM— se rechaza.
Cada muestra renueva una lease de cinco segundos. Si el publicador desaparece sin cerrar, el
siguiente acceso elimina la sesión inactiva y permite una nueva captura sin reiniciar el daemon.
Secuencia y reloj monotónico deben avanzar estrictamente; una regresión rechaza la publicación
completa sin alterar la última medición ni el estado de voz.
Al finalizar un turno, `duration_milliseconds` debe coincidir exactamente con el tiempo transcurrido
entre los eventos monotónicos de inicio y fin; una discrepancia también falla atómicamente.
El evento final solo es válido junto a una muestra con `voice_active=false`; el daemon no permite
que el estado del turno contradiga la medición que lo produjo.

`voice.submit` acepta exclusivamente un transcript final marcado como on-device, fuerza las
modalidades `audio` y `text` y lo procesa mediante la misma cola segura que `swarm.submit`. El texto
continúa sujeto al filtro de secretos antes de cualquier llamada NVIDIA. Como no transmite audio
crudo, el router selecciona el especialista por el significado del transcript y no por su modalidad
de origen. Cada `capture_id` se consume una sola vez dentro de una ventana efímera de las 256
capturas más recientes; repetirlo con una solicitud IPC nueva no crea otro job.

`image.submit` acepta una instrucción y una única imagen PNG, JPEG o WebP. La imagen decodificada se
limita a 32 KiB, su firma debe coincidir con el MIME declarado y solo se envía al especialista con
capacidad visual. El router, los asesores de texto, el sintetizador, la memoria y la auditoría nunca
reciben ni persisten el Base64.

Las solicitudes del enjambre se ejecutan como trabajos asíncronos en memoria con estados `queued`,
`running`, `completed`, `failed` y `cancelled`. La cola está acotada, elimina primero resultados
terminales antiguos y nunca devuelve detalles internos de excepciones. Los resultados públicos se
limitan a 24 KiB de cadena JSON serializada para respetar el framing aun con caracteres de escape.
Al detener el daemon se cancelan todos los trabajos activos; reiniciarlo no restaura trabajos
anteriores. Cada ejecución del grafo dispone de un presupuesto total de 120 segundos; al agotarlo
termina con `swarm_execution_timeout` y libera el cupo del job.

El registro append-only `audit.jsonl` está limitado a 16 MiB para que un daemon permanente no pueda
consumir disco o memoria sin cota. `AEGIS_AUDIT_MAX_BYTES` permite ajustarlo entre 64 KiB y 256 MiB.
Al alcanzar el límite, las nuevas operaciones auditadas fallan antes de ejecutarse. No hay truncado
ni rotación automática porque romperían silenciosamente la cadena criptográfica.
El directorio y el archivo se revalidan en cada escritura y verificación: deben ser objetos reales,
pertenecer al UID del daemon y negar acceso a grupo/otros. Un enlace simbólico o un cambio de permisos
hace que `security.status` reporte `compromised` y bloquea nuevos turnos de voz.
Durante la vida del daemon también se anclan en memoria la identidad, longitud y punta observadas:
eliminar, sustituir, truncar o reescribir el historial produce el mismo cierre seguro. Tras reiniciar
se establece una ancla nueva; la continuidad durable entre reinicios no forma parte de este MVP.
Una vez detectada una pérdida de integridad o un error de I/O, la sesión conserva `compromised`
aunque el archivo parezca restaurado; solo reiniciar el daemon crea una sesión de confianza nueva.
El daemon es el único escritor autorizado: una cadena externamente anexada, aunque sea válida,
también revoca la confianza durante la sesión activa.

## Memoria persistente

La base local se guarda por defecto en
`~/Library/Application Support/Aegis/memory.sqlite3`. Tanto el directorio como la base deben
pertenecer al usuario y tener permisos `0700` y `0600`; se rechazan enlaces simbólicos, archivos no
regulares, esquemas desconocidos y bases con una identidad de aplicación diferente.
La identidad y los permisos del directorio se anclan durante la sesión y se revalidan antes y
después de abrir cada conexión; sustituirlo o ampliar su acceso bloquea lecturas y escrituras.
Los sidecars SQLite `-journal`, `-wal` y `-shm` se validan antes de conectar y se aseguran mediante
descriptores que no siguen enlaces simbólicos.

Cada registro pertenece a un namespace explícito y se clasifica como `episodic`, `preference`,
`semantic` o `summary`. La recuperación textual usa FTS5 con consultas parametrizadas, resultados
acotados y extractos de hasta 768 bytes. La capacidad nunca provoca borrado automático: una base
llena rechaza nuevas escrituras. Los borrados usan `secure_delete` y exigen namespace e ID exactos.
Esta base no es un almacén de credenciales; patrones evidentes de claves se rechazan y los secretos
continúan residiendo exclusivamente en Keychain.
Antes de devolver memoria, resultados RAG o turnos, Jarvis recomputa `content_sha256` y rechaza filas
alteradas. El hash detecta corrupción, pero no autentica frente a quien pueda reescribir contenido y
hash; esa garantía requeriría una clave y política de rotación separadas.

FTS5 constituye el primer nivel determinista de RAG local. El esquema v2 puede almacenar vectores
`float32` normalizados y combinar ranking léxico y semántico mediante Reciprocal Rank Fusion. La
búsqueda vectorial se limita por defecto a las 2.000 memorias indexadas más recientes para mantener
latencia y consumo de RAM previsibles en Apple Silicon.

Los embeddings remotos están desactivados por defecto. Para aceptar explícitamente que el contenido
indexado y las consultas se envíen al endpoint NVIDIA NIM, se configura:

```bash
export AEGIS_MEMORY_REMOTE_EMBEDDINGS_ENABLED=true
```

El modelo configurado es `nvidia/nemotron-3-embed-1b` y se consume mediante
`https://integrate.api.nvidia.com/v1/embeddings`; no se descarga ningún modelo. Si el endpoint falla
o aplica rate limiting, la recuperación continúa con FTS5 local. LangGraph consulta siempre el
namespace fijo `user.default` —configurable por el operador, no por el prompt— después del routing,
y recibe un máximo de 4 KiB de extractos marcados explícitamente como datos no confiables.

## Continuidad conversacional

El esquema SQLite v3 persiste intercambios completos `user`/`assistant` con secuencia monotónica y
hash SHA-256. Los jobs de una misma conversación se serializan; conversaciones distintas pueden
ejecutarse en paralelo. Solo se escribe cuando el grafo produce una respuesta válida y ambos turnos
se insertan en una transacción. Un fallo o cancelación anterior al resultado no deja turnos
parciales.

Por defecto se permiten 1.000 conversaciones por namespace y 1.000 turnos por conversación, sin
expulsión silenciosa. LangGraph recibe los 12 turnos recientes dentro de un presupuesto adicional de
4 KiB, siempre como contexto no confiable. La respuesta IPC de historial limita cada contenido a
4 KiB por turno ya serializado como JSON y devuelve `truncated=true` cuando corresponde, manteniendo
el frame total por debajo de 64 KiB incluso ante caracteres de escape.

`swarm.submit` rechaza patrones inequívocos de credenciales antes de crear el job, por lo que ese
material no se envía al endpoint NVIDIA. El filtro de persistencia permanece como segunda defensa
para llamadas internas y contenido producido por el proveedor; en ese caso el job informa
`conversation_persisted=false`. Al reanudar explícitamente una sesión, su historial acotado sí se
envía al modelo NVIDIA especialista.

## Audio local

El paquete SwiftPM [`native/AegisAudio`](native/AegisAudio) compila un helper nativo `arm64` que usa
`AVAudioEngine` para captura acotada y Accelerate/vDSP para RMS y pico. No descarga modelos, no abre
red, no persiste voz y no inicia escucha permanente.

La detección de turnos usa histéresis local: exige actividad sostenida para encender el estado
`speaking` y silencio sostenido para apagarlo. Los eventos solo contienen UUID efímero, secuencia,
reloj monotónico y duración; no son un *wake word*, transcripción ni identificación del hablante.

El detector opcional de la palabra “Jarvis” usa `AVAudioEngine`, SoundAnalysis y Core ML local,
desactivado por defecto. Procesa buffers efímeros sin archivos ni red y exige dos clasificaciones
consecutivas donde `jarvis` sea la primera etiqueta con confianza mínima de 0,85. Aplica cinco
segundos de enfriamiento y activa la transcripción completa solo después de confirmar la palabra.
El turno termina por silencio con un límite defensivo; el audio anterior a la activación nunca llega
al daemon ni a NVIDIA. La identificación del hablante sigue siendo una defensa posterior.
Antes de que macOS entre en reposo, Jarvis detiene el motor de audio; al despertar espera de nuevo el
intervalo acústico estable y reanuda la escucha solo si el usuario conserva el opt-in y el modelo
sigue siendo válido. No mantiene polling energético ni consume el reintento por fallos de hardware.
Una concesión posterior del permiso de Micrófono inicia el detector sin reiniciar la app; una
revocación lo detiene en el siguiente sondeo de estado. Esta reconciliación solo responde a cambios
reales de TCC y nunca convierte un fallo estable del stream en reintentos periódicos.
El mismo sondeo detiene SoundAnalysis cuando el daemon no está disponible o la auditoría deja de
estar íntegra. La escucha se reanuda cuando ambos se recuperan, sin consumir CPU ni micrófono durante
un estado en el que Jarvis no podría procesar la activación.
En el MacBook Air fanless, Jarvis también pausa el detector ante presión térmica `serious` o
`critical` y lo reanuda al volver a `nominal` o `fair`. La transición usa la notificación nativa de
macOS, sin polling ni lectura de sensores privados.
Al activar Modo de bajo consumo, macOS pausa igualmente la frase de activación y conserva disponibles
el atajo global y las acciones explícitas. Desactivar ese modo agenda la misma reanudación acústica;
no existe una preferencia energética duplicada dentro de Jarvis.
La Menu Bar muestra una única causa agregada mientras la escucha está pausada: audio en uso, sistema,
servicio no disponible, presión térmica, bajo consumo o reanudación. La causa es efímera y nunca
incluye detalles del daemon, auditoría, hardware ni contenido de voz.

La frontera del modelo ya falla de forma cerrada. Jarvis solo reconoce como candidato el activo
firmado `JarvisWakeWord.mlmodelc` si SoundAnalysis lo valida como clasificador de audio, contiene la
etiqueta exacta `jarvis` y expone como máximo 16 clases. La Menu Bar informa si está pendiente,
inválido o disponible. Solo un modelo válido muestra la acción explícita para habilitar la escucha;
la elección se conserva localmente y puede desactivarse desde el mismo menú. El stream se pausa
durante captura, enrolamiento y voz sintetizada. No existe fallback mediante transcripción continua.

El entrenador local `jarvis-wake-word-trainer` usa Create ML y Audio Feature Print; no enlaza sus
dependencias de entrenamiento con la aplicación. Exige un dataset externo al repositorio con las
carpetas exactas `jarvis/` y `background/`, al menos 20 clips reales por clase y límites estrictos
de formato, duración, tamaño y cantidad. Rechaza una validación con más de 25 % de error, incluye el
fingerprint SHA-256 del dataset en el modelo y nunca sobrescribe un activo existente. Se ejecuta con:

```bash
./script/train_wake_word.sh /ruta/al/dataset
```

La Menu Bar ofrece `Preparar activación por “Jarvis”…` como recolector mínimo del dataset. La
ventana espera un segundo tras pulsar uno de sus botones y solo abre el micrófono al mostrar
`Grabando`: un clip CAF local de dos segundos para
`jarvis` o para `background`. Exige 20 muestras por clase, limita cada clase a 100, crea directorios
`0700` y archivos `0600`, y rechaza enlaces o contenido inesperado. No inicia entrenamiento, escucha
continua ni red. Accelerate descarta positivos con menos de 120 ms de señal audible y cualquier clip
saturado; los negativos sí admiten silencio real. Con el dataset mínimo completo, el comando puede
omitir la ruta:

```bash
./script/train_wake_word.sh
```

La misma ventana permite `Eliminar muestras…` únicamente después de una confirmación destructiva.
Valida todas las muestras antes de borrar alguna, conserva los directorios privados y no elimina un
modelo ya entrenado.

El flujo recomendado automatiza validación, entrenamiento, empaquetado, instalación y reinicio. Si
un modelo válido ya existe, reanuda desde el empaquetado sin entrenar ni sobrescribirlo:

```bash
./script/activate_wake_word.sh
```

El resultado privado se guarda en
`~/Library/Application Support/Aegis/Models/JarvisWakeWord.mlmodelc`; las grabaciones no se copian ni
se incorporan a Git. El empaquetado de Jarvis detecta ese modelo local y lo incluye en el bundle.

Durante el push-to-talk, el mismo medidor entrega al HUD únicamente `activity` normalizada entre
0 y 1. Ese `Float` efímero modula la escala de la esfera y vuelve a cero al terminar la captura; no
se registra, persiste ni envía por IPC, y el buffer PCM nunca sale del procesador de audio.

La transcripción usa Apple Speech en modo push-to-talk y configura
`requiresOnDeviceRecognition=true`; no descarga modelos ni permite fallback remoto. El helper
expone `speech-status`, `transcribe` y `transcribe-submit`, pero nunca solicita permisos TCC. El
último verifica primero el daemon, captura localmente y publica solo el transcript final mediante
`voice.submit`; la credencial IPC se recupera de Keychain y nunca viaja en argumentos, variables de
entorno ni logs. Los permisos pertenecen al bundle instalado, no al helper de terminal. En este host
Micrófono y Speech están autorizados explícitamente para `Jarvis`; la captura real sigue
ocurriendo solo al pulsar `Hablar`.

La acción explícita `Preguntar sobre imagen…` usa `NSOpenPanel` y `ImageIO`, sin dependencias
externas. Normaliza localmente la selección a JPEG de máximo 32 KiB, captura una instrucción con
Apple Speech on-device y envía únicamente ese texto final junto al adjunto por `image.submit`.
Libera el archivo inmediatamente y no persiste la ruta, la miniatura, el Base64 ni el audio.

`Preguntar sobre pantalla` requiere una acción separada y permiso TCC de Screen Recording.
ScreenCaptureKit obtiene una sola imagen del display principal, excluyendo el proceso Jarvis, cursor
y audio; la normaliza en memoria y reutiliza el mismo prompt hablado e IPC multimodal. Un fallo de
permiso o de exclusión cancela el turno y ninguna captura se escribe en disco.

```bash
./script/test_native.sh
./script/build_and_run.sh --verify
```

`test_native.sh` fija el SDK arm64 compatible y carga Swift Testing desde el Command Line
Toolchain; no descarga dependencias ni depende de artefactos previos.

La consulta de permisos es solo lectura. `Jarvis` consulta el daemon en segundo plano y solicita
TCC únicamente al pulsar la acción correspondiente; no abre micrófono ni Speech al arrancar. Es una
app `LSUIElement` sin Dock y sin ventana convencional. `Hablar` solo se habilita con daemon y
permisos disponibles: descarta eventos parciales, conserva el transcript final únicamente durante
el envío IPC autenticado y no muestra ni registra su contenido. Tras enviar, consulta el job durante
un máximo de 60 segundos y pronuncia localmente hasta 2.000 caracteres de la respuesta mediante
`AVSpeechSynthesizer`; una nueva captura interrumpe la voz anterior.

En pantallas integradas con notch, Jarvis mantiene una presencia nativa mínima alrededor del recorte:
dos indicadores y una línea inferior reflejan voz, procesamiento, aprobación y seguridad. Es un
`NSPanel` transparente y no activante; al pulsar cualquiera de sus alas se expande sin abrir un chat
y presenta una consola neural compacta: núcleo reactivo, estado de integridad, acción principal de
voz, acceso al HUD e identidad local ARM64. Las señales laterales y el núcleo cambian con amplitud,
procesamiento, aprobación o fallo; en reposo no mantienen animaciones continuas. Ejecutar una acción
vuelve a contraerlo. Si faltan permisos, la acción principal cambia a `CONFIGURAR VOZ` o
`AJUSTAR VOZ` y solo entonces solicita TCC o abre Privacidad; nunca lo hace al arrancar. No usa
monitores globales, permisos de Accesibilidad ni sustituye el acceso de Menu Bar. En pantallas sin
notch no se crea el panel y no añade sondeo IPC permanente.

```bash
./script/build_and_run.sh --verify
```

El script construye el producto SwiftPM, genera un bundle arm64 con las descripciones TCC, aplica
firma local y lanza una copia efímera desde `/private/tmp`. `dist/Jarvis.zip` evita que los
xattrs de FileProvider invaliden la firma dentro de Documents. La captura manual `meter` exige
autorización previa y se limita a 60 segundos.

Para instalar el bundle firmado en `~/Applications` y arrancarlo automáticamente al iniciar sesión:

```bash
./script/menu_bar_service.sh install
./script/menu_bar_service.sh status
./script/menu_bar_service.sh permissions
./script/menu_bar_service.sh voice-turn
./script/menu_bar_service.sh hud
```

`uninstall` desactiva el autoinicio y cierra la app, pero conserva el bundle instalado para evitar
una eliminación destructiva implícita. `permissions` relanza explícitamente la app instalada y
solicita únicamente los permisos todavía indeterminados; macOS conserva la decisión final del
usuario y el arranque normal nunca solicita TCC.
`voice-turn` relanza el mismo bundle, emite un beep nativo y realiza una única captura explícita.
Termina tras 1,2 segundos de silencio, espera como máximo ocho segundos para que el usuario empiece
a hablar y aplica un límite total defensivo de 60 segundos. La telemetría unificada conserva solo
etapas y códigos de fallo; nunca audio, transcript, respuesta ni `job_id`.

`hud` relanza el bundle y abre explícitamente una ventana transparente no restaurable. La misma
acción está disponible como “Mostrar HUD…” en la Menu Bar. El HUD usa SceneKit nativo para renderizar
210 nodos en siete clústeres y consulta `swarm.activity` solo mientras permanece visible; cerrarlo
detiene el sondeo y borra el estado visual. No aparece al iniciar sesión.

`⌃⇧Espacio` inicia el mismo turno de voz desde cualquier aplicación sin abrir el menú ni el HUD.
El atajo usa `RegisterEventHotKey`, no monitoriza pulsaciones y no requiere Accesibilidad o Input
Monitoring. Sigue pasando por los controles existentes de permisos, integridad y exclusión mutua.

Si el especialista propone un sondeo TCP o un diagnóstico local fijo, el job entra en
`awaiting_confirmation` durante un máximo de dos minutos. La Menu Bar muestra únicamente
“Aprobación pendiente”; el usuario debe abrir de forma explícita una ventana singleton para revisar
la operación. “Aprobar una vez” ejecuta esa misma llamada sin repetir la inferencia, y “Denegar”
reutiliza `jobs.cancel`.

## Frontera de herramientas

Los modelos reciben únicamente los esquemas compatibles con su rol. Cada llamada propuesta se
valida con argumentos estrictos, alcance local y nivel de riesgo. Las operaciones de riesgo alto o
crítico requieren una confirmación de un solo uso ligada al identificador, agente, herramienta y
argumentos exactos de la llamada. La solicitud visual vence a los dos minutos, el grant interno se
consume atómicamente al aprobar y todo estado se invalida al reiniciar el proceso. El grafo produce
veredictos `allow`, `require_confirmation` o `deny`; una confirmación pendiente detiene el grafo
antes del synthesizer y solo admite una acción en este MVP.

Los ejecutores habilitados se limitan a metadatos no secretos del runtime, archivos UTF-8 regulares
dentro del workspace, sondeo TCP local acotado y cuatro diagnósticos locales fijos: estado Git,
inventario de procesos, listeners TCP y postura de seguridad de macOS (SIP, Gatekeeper, FileVault y
firewall). Terminal no acepta comandos, shell, tuberías ni argumentos libres; usa binarios nativos
absolutos, entorno mínimo, timeout y salida acotada. La lectura de
archivos usa descriptores relativos y no sigue enlaces simbólicos. El log de auditoría del daemon
conserva decisiones, códigos de resultado, tamaño y SHA-256 de la salida; no almacena el contenido
producido por una herramienta.

Unified Logging recibe únicamente transiciones agregadas del monitor de integridad (`intact`,
`compromised` o `unavailable`), nunca registros, hashes, rutas, procesos, sockets ni argumentos.
