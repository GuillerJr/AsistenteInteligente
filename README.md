# Aegis Swarm

Núcleo local, auditable y *voice-first* para un asistente táctico multiagente en macOS Apple Silicon.

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
| 3. Sensores | en progreso | turno de voz y respuesta hablada locales |
| 4. Ciberseguridad | en progreso | broker, confirmaciones y sondeo TCP local acotado |
| 5. Interfaz | en progreso | Menu Bar con autoinicio; HUD 3D excluido |

## Seguridad

La API key no debe guardarse en el repositorio ni en archivos `.env`. El servicio de Keychain usado por defecto es `ai.aegis.nvidia-nim`, con la cuenta `default`.

El descubrimiento de red solo usa conexiones TCP nativas contra IP/CIDR de loopback o redes privadas
autorizadas. Cada llamada exige confirmación exacta de un solo uso, entre uno y ocho puertos, y un
máximo de 256 direcciones. No ejecuta shell, `ping` ni `nmap`; tampoco hace DNS, fingerprinting,
envía payloads o persiste resultados. El ejecutor vuelve a validar alcance y límites aunque reciba
una autorización falsificada.

## Entorno

El proyecto requiere Python 3.11, 3.12 o 3.13 nativo `arm64`. La configuración evita depender de contenedores o binarios x86 para el núcleo local.

```bash
uv sync --all-groups --no-editable --python /opt/homebrew/bin/python3.11 --cache-dir .uv-cache
uv run --no-sync pytest
./script/aegis.sh doctor
```

`aegis doctor` verifica arquitectura, configuración y presencia de la credencial sin imprimirla.
`aegis probe-nvidia` realiza una inferencia mínima y solo informa estado y modelo, nunca el secreto.
`aegis probe-nvidia-embedding` verifica el endpoint de embeddings con una frase sintética y solo
informa modelo y dimensiones; nunca imprime el vector ni la credencial.
`aegis verify-audit <ruta>` comprueba permisos, secuencia y cadena hash del registro local.

## Daemon local

```bash
./script/aegis.sh daemon
./script/aegis.sh daemon-status
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

El daemon escucha por defecto en `~/Library/Application Support/Aegis/aegis.sock`. El directorio y
el socket requieren permisos `0700` y `0600`, respectivamente. Cada conexión valida el UID del
proceso cliente mediante credenciales nativas de macOS; cada mensaje y respuesta se autentica además
con HMAC-SHA256 usando un secreto independiente guardado en Keychain bajo `ai.aegis.ipc-auth`.

El protocolo `1.0` limita cada frame a 64 KiB, acepta una solicitud por conexión y rechaza timestamps
fuera de ventana, nonces repetidos, métodos desconocidos y payloads inesperados. Expone `health`,
`runtime.info`, `swarm.submit`, `swarm.activity`, `voice.submit`, `jobs.status` y `jobs.cancel`.
`jobs.approve` consume exclusivamente la confirmación pendiente del digest exacto.

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

`voice.submit` acepta exclusivamente un transcript final marcado como on-device, fuerza las
modalidades `audio` y `text` y lo procesa mediante la misma cola segura que `swarm.submit`. El texto
continúa sujeto al filtro de secretos antes de cualquier llamada NVIDIA. Como no transmite audio
crudo, el router selecciona el especialista por el significado del transcript y no por su modalidad
de origen.

Las solicitudes del enjambre se ejecutan como trabajos asíncronos en memoria con estados `queued`,
`running`, `completed`, `failed` y `cancelled`. La cola está acotada, elimina primero resultados
terminales antiguos y nunca devuelve detalles internos de excepciones. Los resultados públicos se
limitan a 24 KiB de cadena JSON serializada para respetar el framing aun con caracteres de escape.
Al detener el daemon se cancelan todos los trabajos activos; reiniciarlo no restaura trabajos
anteriores.

## Memoria persistente

La base local se guarda por defecto en
`~/Library/Application Support/Aegis/memory.sqlite3`. Tanto el directorio como la base deben
pertenecer al usuario y tener permisos `0700` y `0600`; se rechazan enlaces simbólicos, archivos no
regulares, esquemas desconocidos y bases con una identidad de aplicación diferente.

Cada registro pertenece a un namespace explícito y se clasifica como `episodic`, `preference`,
`semantic` o `summary`. La recuperación textual usa FTS5 con consultas parametrizadas, resultados
acotados y extractos de hasta 768 bytes. La capacidad nunca provoca borrado automático: una base
llena rechaza nuevas escrituras. Los borrados usan `secure_delete` y exigen namespace e ID exactos.
Esta base no es un almacén de credenciales; patrones evidentes de claves se rechazan y los secretos
continúan residiendo exclusivamente en Keychain.

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

La transcripción usa Apple Speech en modo push-to-talk y configura
`requiresOnDeviceRecognition=true`; no descarga modelos ni permite fallback remoto. El helper
expone `speech-status`, `transcribe` y `transcribe-submit`, pero nunca solicita permisos TCC. El
último verifica primero el daemon, captura localmente y publica solo el transcript final mediante
`voice.submit`; la credencial IPC se recupera de Keychain y nunca viaja en argumentos, variables de
entorno ni logs. Los permisos pertenecen al bundle instalado, no al helper de terminal. En este host
Micrófono y Speech están autorizados explícitamente para `AegisMenuBar`; la captura real sigue
ocurriendo solo al pulsar `Hablar 8 s`.

```bash
cd native/AegisAudio
swift build
swift test
.build/debug/aegis-audio-helper permission
.build/debug/aegis-audio-helper ipc-health
```

El comando de permiso es solo lectura. `AegisMenuBar` consulta el daemon en segundo plano y solicita
TCC únicamente al pulsar la acción correspondiente; no abre micrófono ni Speech al arrancar. Es una
app `LSUIElement` sin Dock y sin ventana convencional. `Hablar 8 s` solo se habilita con daemon y
permisos disponibles: descarta eventos parciales, conserva el transcript final únicamente durante
el envío IPC autenticado y no muestra ni registra su contenido. Tras enviar, consulta el job durante
un máximo de 60 segundos y pronuncia localmente hasta 2.000 caracteres de la respuesta mediante
`AVSpeechSynthesizer`; una nueva captura interrumpe la voz anterior.

```bash
./script/build_and_run.sh --verify
```

El script construye el producto SwiftPM, genera un bundle arm64 con las descripciones TCC, aplica
firma local y lanza una copia efímera desde `/private/tmp`. `dist/AegisMenuBar.zip` evita que los
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
`voice-turn` relanza el mismo bundle, emite un beep nativo y realiza una única captura explícita de
ocho segundos. La telemetría unificada conserva solo etapas y códigos de fallo; nunca audio,
transcript, respuesta ni `job_id`.

`hud` relanza el bundle y abre explícitamente una ventana transparente no restaurable. La misma
acción está disponible como “Mostrar HUD…” en la Menu Bar. El HUD usa SceneKit nativo para renderizar
210 nodos en siete clústeres y consulta `swarm.activity` solo mientras permanece visible; cerrarlo
detiene el sondeo y borra el estado visual. No aparece al iniciar sesión.

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
dentro del workspace, sondeo TCP local acotado y tres diagnósticos locales fijos: estado Git,
inventario de procesos y listeners TCP. Terminal no acepta comandos, shell, tuberías ni argumentos
libres; usa binarios nativos absolutos, entorno mínimo, timeout y salida acotada. La lectura de
archivos usa descriptores relativos y no sigue enlaces simbólicos. El log de auditoría del daemon
conserva decisiones, códigos de resultado, tamaño y SHA-256 de la salida; no almacena el contenido
producido por una herramienta.

Unified Logging recibe únicamente transiciones agregadas del monitor de integridad (`intact`,
`compromised` o `unavailable`), nunca registros, hashes, rutas, procesos, sockets ni argumentos.
