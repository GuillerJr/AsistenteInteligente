# Telegram, voz, documentos y multimedia — integración propia

Estado: `qualified_selected_increment` (29-10-2026).
Se cierra el incremento finito elegido de Telegram, voz local, documentos y
multimedia en macOS Apple Silicon. Este registro distingue implementación, pruebas,
CI y recorridos reales; no declara completas las familias amplias ni la fase A.
La fase B no se inicia. La app del dueño y sus perfiles anteriores se conservan.

La [CI completa de `9d4edfb`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40982348625)
aprobó Python (3526 casos en Ubuntu con 46 omitidos y 3572 en macOS), Swift
(634 casos, uno omitido y cero fallos), bundle/helper, paquetes, smoke y limpieza.
Pasaron los 13 casos del monitor de captura, los ocho del catálogo de adjuntos,
los tres del navegador y los 12 contratos del lanzador. Las suites y compilaciones
se ejecutaron en los runners remotos; ese éxito no acredita por sí solo TCC,
voz o GUI. El [registro](PHASE_A_TELEGRAM_VOICE_MEDIA_QUALIFICATION.json) fija el
recibo de CI con SHA-256 y conserva los intentos fallidos y las identidades de
cada recorrido real.

La revisión anterior `f6864b1` tuvo un fallo del test de revocación de configuración
en Ubuntu (3525 aprobados, 46 omitidos y un fallo). La prueba intentaba cambiar
el journal mientras su lease de lectura impedía correctamente esa escritura.
`450621f` corrigió sólo ese oráculo mediante el guard de configuración del host,
sin cambiar el runtime. Swift llegó a completar 628 casos sin fallos antes de
cancelarse aquella CI; Python macOS y smoke no terminaron, por lo que ese intento
parcial no se presenta como aprobado.

La revisión funcional histórica `628ab0e` corrigió el monitor de cambios de
entrada. Su [CI](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40941195038)
aprobó Ubuntu y compiló el bundle/helper, pero Swift tuvo un fallo entre 614
casos (uno omitido). Los 13 casos del monitor pasaron; falló el oráculo asíncrono
de cancelación del navegador. Se preparó una corrección sólo de esa prueba.
MacPython, paquetes y smoke posteriores no se ejecutaron: en ese checkpoint
histórico faltaban la CI completa del nuevo commit y la repetición real de voz
con su bundle. La [CI completa de `f5e78ea`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40943870771)
aprobó después 3448 casos Python en Ubuntu (46 omitidos), 3494 en macOS y 614
casos Swift (uno omitido, cero fallos), incluidos los 13 del monitor y los tres
del navegador. También pasaron bundle/helper, paquetes, smoke y limpieza. Su
artefacto quedó preparado y todavía no se lanzó: la CI no acredita voz ni TCC.

Telegram acredita privado y grupo separados, documento con marcador/fuente,
envío recibido por el dueño, rechazo sin subida, retirada con original intacto
y reactivación sin ejecutar los mensajes de la pausa. El corpus mixto con Codex
real acredita contenido y fuentes de DOCX/CSV, imagen y vídeo parcial después de
corregir la entrega conjunta de texto e imágenes aprobados. WAV/Ogg se
transcribieron localmente; el dueño confirmó una lectura audible y dictado sin
envío automático.

Dos notas de voz nuevas recibidas realmente en el grupo, de 109420 y 58701
bytes, acreditaron importación aislada, procesamiento local aprobado y respuesta
correlacionada del bot. Ambas inspecciones terminaron conocidas y verificadas,
pero ninguna recuperó el marcador esperado. La causa sigue sin establecerse
entre captura y reconocimiento. Se conservan sus huellas y resultados fallidos;
no se transcribe aquí contenido que podría proceder del ambiente. Este recorrido
no cierra la fidelidad de voz de Telegram y no se harán más grabaciones del
entorno para repetirlo.

También se observó una detención con `telegram_transport_failed`: no había
eventos activos ni efectos nuevos inciertos, y el Ogg de control enviado
mientras el lector estaba inactivo no se importó. La reactivación explícita de
las mismas rutas admitió un mensaje nuevo con marcador 7721. Ese resultado
acredita reanudación de texto, sin replay; no acredita la llegada de aquel Ogg
ni establece la causa concreta de red.

La segunda nota reveló un fallo del catálogo: los manifiestos v1 no tienen hora
de importación ni relación con el ingreso, y su orden por ID no identifica la
«última nota» entre nombres iguales. Se escribió un catálogo v2 con secuencia por
sesión bajo el bloqueo de publicación, hora UTC en milisegundos y procedencia
mínima mediante ejecución interna y tipo de adjunto. La secuencia define el
orden incluso si retrocede el reloj; los originales v1 conservan su cronología
desconocida y no se reescriben. Estos metadatos no autorizan lecturas ni envíos.
El cambio y sus pruebas pasaron la CI completa de `450621f`; en aquel checkpoint
quedaba el recorrido con dos notas de igual nombre. El recorrido privado de
`9d4edfb` descrito más abajo acreditó después la selección exacta de la última.

En el mismo checkpoint se escribieron recuperación acotada sólo para fallos
conocidos del GET de actualizaciones y envío explícito `delivery=voice` mediante
[sendVoice oficial](https://core.telegram.org/bots/api#sendvoice). Este último
acepta originales Ogg OPUS de hasta 5 MiB y 120 segundos, verificados localmente
antes de una sola subida aprobada al destino de origen. El modo predeterminado
sigue siendo documento. No cambia el audio ni captura micrófono; un ACK incierto
no habilita reintentos. Ambos cambios pasaron la CI completa de `450621f`.
En aquel checkpoint faltaba el recorrido integrado; posteriormente se enviaron y
reenviaron dos originales Opus sintéticos como notas nativas para verificar
marcadores y orden sin captar el ambiente, con el resultado registrado más abajo.

Los ocho formatos adicionales acreditaron una inspección por archivo, recibos
conocidos, originales y copias CAS con huellas coincidentes y marcadores/citas
recuperados por Codex: XLSX, PPTX, RTF, HTML inerte, JSON, XML, TSV y PDF escaneado.
En el PDF con tipografía segmentada, el OCR produjo `TARLIS Y2` y la lectura
visual `JARVIS 42`; esa discrepancia sigue registrada. Otro PDF sin capa de
texto, con letra regular, recuperó desde el recibo OCR literal
`MARCADOROCRZAFIRO3582` y `VALOR 76`, con cita `page:1`. No se afirma exactitud
universal del OCR ni se sustituye el fallo anterior por este resultado.

La app rechazó una inspección aprobada de PDF cifrado con
`media_native_pdf_encrypted`, sin repetirla ni probar contraseñas. El archivo de
16 MiB más un byte se rechazó antes de presentar revisión/importación; su nombre
y SHA no aparecen en el catálogo y el original conserva su huella. La solicitud
exacta de `media_inspect` sobre el adjunto retirado también quedó bloqueada:
Permitir/Recordar deshabilitados, rechazo nativo y cero recibos de ejecución,
claims o CAS nuevos. Este caso acredita el gate previo al despacho; no inventa
una respuesta «revoked» del ejecutor que nunca se llegó a llamar.

El intento inicial de probar la revisión 201a5b7 falló porque su lanzador de QA
declaraba un script como ejecutable principal y la solicitud de Speech abortó
por TCC. El informe del proceso propio acredita ausencia de bundle ID y
atribución a otro proceso; no acredita un recorrido de voz satisfactorio. El
paquete original de CI sí declara el Mach-O principal. Con `dbad498`, el helper
de CI inició la app mediante NSWorkspace/LaunchServices, sin wrapper ni Popen de
la interfaz. El dueño concedió Speech desde la app y el aborto anterior no se
repitió. Los dos intentos de micrófono posteriores se detuvieron antes del
estímulo con el aviso de cambio de entrada; conservaron el borrador y no enviaron
un run. La fuente trataba cualquier notificación de configuración como cambio
de entrada, sin comprobar su identidad. La causa concreta de hardware de esos
intentos no quedó establecida. La corrección pasó después la CI completa de
`f5e78ea`; en aquel checkpoint quedaba la repetición real de wake/Talk junto
con los cambios posteriores de Telegram y catálogo. Talk se acreditó después
por separado, como se detalla más abajo. Los procesos del harness fallido se cerraron
de forma explícita y los originales se conservaron. Ver el
[contrato de arranque QA](NATIVE_QA_LAUNCH.md).

El reproductor real acreditó pausa, búsqueda mientras estaba pausado, selección
de auriculares conservando posición, reanudación explícita y audio confirmado por
el dueño. La primera retirada física reportada no era una desconexión de
CoreAudio y se conserva como intento no cualificado. Tras desconectar realmente
esa salida, la UI la retiró, detuvo el reproductor y no pasó al altavoz. Cerrar y
abrir de nuevo volvió a posición cero, pausado y sin reutilizar la selección
anterior. El preview Ogg distinguió original y WAV derivado en RAM y avanzó tras
reproducirlo hasta el final de 3,80425 segundos; ese clip no tiene confirmación
acústica independiente. El vídeo llegó al final de dos segundos, permitió buscar
al segundo uno y mostró los colores y marcador esperados. El recibo suplementario
conserva el original de las pruebas anteriores. No se midió
la salida global antes de las primeras selecciones ni se infiere el instante
interno de liberación de memoria desde la UI.

El recorrido `dbad498` terminó con Salir en la interfaz. El supervisor devolvió
cero únicamente tras confirmar limpieza de las siete identidades observadas
incluido el helper, sin señales ni incidencias, y la liberación del lease
exacto del perfil. Observó un máximo de 346034176 bytes durante 3488,88 segundos,
dentro del presupuesto observado de 384 MiB. Esto no proporciona un código
`waitpid` de salida de la app nativa ni una cuota física dura. Con autorización
del dueño se subió temporalmente la salida de volumen cero/silenciada a 0,3/no
silenciada para el segundo intento; tras el cierre se restauró cero/silenciada,
sin alterar rutas, balance o emparejamiento. No se deduce de ello la causa del
fallo de transcripción.

El primer intento de iniciar `450621f` terminó con `launchServices` y efecto
`unknown`, sin PID devuelto. El helper terminó y se unió al supervisor; una
reconciliación posterior comprobó la ausencia de sus dos PIDs conocidos y que
el lease exacto del perfil estaba libre. Esto no convierte el efecto histórico
en «no ocurrió». Las consultas de logs limitadas al PID, CID y rutas sintéticas
no devolvieron eventos; la causa del rechazo no está establecida.

El segundo intento fue una operación separada, con recibos exclusivos y el mismo
bundle, helper, fuente y estado sintético. Se ejecutó fuera del sandbox de ejecución
del primer intento y NSWorkspace confirmó el PID 84570, con identidad de nacimiento
1793308302.132649 y efecto conocido. El cambio de entorno de ejecución no prueba
la causa del fallo anterior. Se conservan ambos recibos; aquel recibo de arranque
no acreditaba por sí solo voz, TCC ni cierre. La copia de QA conserva el ejecutable Mach-O y
las descripciones de permisos originales del artefacto remoto; no reemplaza la
app instalada del dueño.

Los dos originales Opus se enviaron después con `delivery=voice`: hubo una
aprobación exacta y un recibo conocido con ACK correlacionado por archivo.
Telegram mostró dos burbujas nativas de voz, de tres y cuatro segundos.
Esta observación distingue la entrega visible del ACK, pero no mide los bytes
del servidor. En ese checkpoint quedaban el reingreso y la selección/transcripción
de la última; su avance posterior se registra más abajo.

La activación real iniciada y detenida por el dueño detectó «Jarvis», conservó
«BORRADOR ACTIVACIÓN» y no creó mensajes en el chat temporal. Sin embargo,
añadió también el prefijo «Esto se descarta Jarvis», que debía excluir.
El recorrido queda fallido y conservado. La política toma la primera coincidencia;
una detección anterior podría explicar ese resultado, pero la entrada histórica
del reconocedor no está disponible y la causa no está demostrada. Se añadieron
cinco pruebas y un diagnóstico en RAM con recuentos, posiciones y huella del
sufijo, sin audio ni texto anterior. No se cambia el recorte por el último
«Jarvis» ni se bloquean menciones legítimas posteriores. La instrumentación pasó
después la CI de `076170d`; su atribución al chat y callback tardío pasó la CI
de `9d4edfb`. Su comprobación nativa instrumentada se completó después, como
se detalla en el cierre de este registro.

La conversación local por voz completó tres turnos conocidos en otra sesión,
con cero herramientas: tras una primera frase incompleta y su aclaración,
las preguntas de suma recibieron «4» y «6», ambas audibles según el dueño.
La UI quedó detenida y CoreAudio, consultando sólo el PID verificado de Jarvis,
informó entrada y salida sin ejecución. Se restauró el volumen a cero y
silenciado. El dueño reportó pulsar Detener, pero también se alcanzaron los
tres turnos máximos; no se atribuye el cierre sólo a ese botón ni se infiere
la causa de la primera frase incompleta. Las fases intermedias de micrófono,
modelo y lectura tienen contratos probados; el sondeo real acredita el estado
posterior, no su orden histórico ni precisión universal del reconocimiento.

La CI de `0efc977` terminó con un fallo global: macOS aprobó 3572 casos
Python y 633 Swift (uno omitido, cero fallos), bundle/helper, paquetes y smoke.
Ubuntu aprobó 3525 casos y omitió 46, pero el oráculo de recuperación reutilizaba
un cliente de sondeo de 200 ms para `runs.start`, que agotó ese plazo antes de
la comprobación de caída. Se separó y cerró el sondeo, devolviendo un cliente
operativo normal sin repetir acciones ni cambiar el runtime. El fallo se
conserva; en aquel checkpoint era necesaria una CI completa nueva antes de
lanzar la app con el diagnóstico. Esa validación se obtuvo a continuación.

La [CI completa de `076170d`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40978876012)
aprobó después 3526 casos Python en Ubuntu (46 omitidos), 3572 en macOS y
633 Swift (uno omitido, cero fallos), bundle, paquetes y smoke. La revisión
posterior liga el diagnóstico en RAM a su conversación y contexto de ejecución,
con una regresión de cambio de chat y callback tardío. Pasó completa la CI de
`9d4edfb` antes de abrir su copia de prueba. No modifica la política del primer
trigger.

La copia 450 alcanzó el plazo de 3600 segundos y el supervisor envió SIGTERM
a sus procesos de app y servicio, sin SIGKILL. La reconciliación confirmó
la salida de las identidades observadas y el mismo bloqueo de perfil libre.
No se presenta como Salir normal por GUI. Los dos reenvíos de voz no se
enviaron: quedaron seleccionados y con el bot filtrado, a la espera de
otro servicio activo. Las confirmaciones previas A/B y Talk se conservan.

La nueva copia `9d4edfb` arrancó con efecto conocido mediante el helper nativo de
su CI, después de verificar el cierre por plazo de 450. Su fuente Python reutiliza
98 archivos de la copia anterior cuyas huellas coinciden con Git en el nuevo
commit. ZIP y TAR se validaron sólo en RAM; se conservan sus huellas, procedencia
y los segmentos `__TEXT` originales comparados con la copia firmada. No se
reemplazó la app instalada. La nueva operación usa un presupuesto observado de
448 MiB: la preparación duró 10,123 segundos y observó un pico de 399138816 bytes.
Esta elección operativa no amplía retroactivamente los 384 MiB del recorrido 450.
Su recorrido y cierre se acreditaron después con los recibos finales descritos
más abajo; la preparación por sí sola no los acreditaba.

El recorrido privado de las dos notas controladas quedó aprobado en su alcance
finito. Los reenvíos llegaron como voz al ámbito privado esperado; el catálogo v2
les asignó las secuencias 3 y 4 y sus huellas coinciden con los originales A y B.
El oráculo final v5 pasó sus ocho comprobaciones, junto con la revisión de las
observaciones nativas y del runtime. La ejecución `b0c0bf68d27442f2a9642d18644fa909`
leyó el catálogo una vez e inspeccionó una vez, con aprobación, la última nota
exacta: secuencia 4, marcador «73» y ausencia del marcador anterior «42».
Se comprobaron los argumentos y su vínculo con la sesión/ruta/revisión, el recibo
local conocido y el ACK correlacionado de la respuesta. El intervalo del recibo
es `time:0-4.222`; la respuesta visible citó `00:00–00:04.222`. El comparador del
literal `time:0-4.222` no encontró esa misma cadena en la respuesta, lo que no se
presenta como ausencia de cita temporal.

El [registro integrado](PHASE_A_TELEGRAM_VOICE_MEDIA_QUALIFICATION.json) fija el
oráculo `qualification-telegram-controlled-private-v5-final.json`, SHA-256
`48393693fa49347bbd39a61e1b0e53edfc821f41d562de45b959e0a0e436b2d9`.
Los cuatro manifiestos v1 y su tombstone permanecen intactos, con cronología
desconocida; los cuatro v2 nuevos se contabilizan por separado. Los fallos de
fidelidad de las grabaciones ambientales del grupo se conservan y este recorrido
privado no los convierte en éxitos ni cualifica esa fidelidad del grupo. Un ACK
del API tampoco demuestra por sí solo identidad de bytes o escucha humana.

La primera repetición instrumentada de wake mostró dos coincidencias, la primera
en la posición 61, 69 caracteres retirados y un sufijo de 44 bytes. El dueño
confirmó haber mencionado «Jarvis» antes de la frase de control: ese resultado es
coherente con la política de primera activación. No establece retroactivamente
qué ocurrió en la captura 450. Al abrir otro chat temporal desaparecieron el
diagnóstico y el borrador del primero; volver al chat original conservó sus
metadatos. Así se comprobó también el aislamiento del diagnóstico en la interfaz.

La captura final controlada quedó aprobada: conservó «BORRADOR CONTROLADO» y
sólo añadió «el marcador es 42.». El diagnóstico registró una coincidencia,
posición 17, 25 caracteres retirados y sufijo de 18 bytes con SHA-256
`f21f84e9c883ca343603b529e2bd890333a9a6ecd83b1073cc8c9700f5856288`.
No se crearon mensajes ni se envió un run. Tras detener la captura, la consulta
CoreAudio limitada al PID propio indicó entrada y salida inactivas. El recibo
`qualification-wake-real-9d-controlled-pass.json` tiene SHA-256
`3f2a8efceaaaf2781c59eb36c84861c883957cbbe37a3a1307dc3f2034edfea6`.

El recorrido original 9d terminó mediante Salir en la interfaz antes de su plazo.
El supervisor finalizó con cero, sin señales ni incidencias, después de 2993,049
segundos y con un pico observado de 403243008 bytes (384,56 MiB) dentro del nuevo
presupuesto de 448 MiB. La reconciliación confirmó la ausencia de las siete
identidades observadas, incluido el supervisor, y el mismo lease de perfil libre.
El recibo `handoff-9d4edfb-v2-close-confirmed.json` tiene SHA-256
`c972f87d8ce7e98eab0872415c4f5002e010cab31366b14758ec0e1063b1c911`.
El cero es del supervisor y acredita su limpieza observada; no es un código de
salida `waitpid` de la app nativa ni una garantía de cuota física dura.

Después de ese cierre, una consulta de interfaz coincidió con la aparición de
otra instancia sintética, PID 96852, sin configuración y sin proceso Python
observado. No se trazó el mecanismo interno exacto de CUA que la abrió. Se cerró
mediante Salir y otra comprobación de metadatos confirmó su ausencia; el recibo
`native-9d4edfb-post-quit-extra-close-confirmed.json` conserva SHA-256
`f7f2246572a566bd5229ed286c7c7da75c844aebd3fd932d88dc99a56536a392`.
No se volvió a leer ni reabrir la app después de ese segundo cierre. Se conserva
esta incidencia de custodia por separado, sin convertirla en parte del recorrido
original ni ocultarla tras su recibo satisfactorio.

El incremento elegido queda cualificado con esos límites: Telegram privado
controlado, rutas de grupo observadas, dictado/wake y Talk local, y el corpus y
reproductor finitos. No implica fidelidad universal de voz, documentos, idiomas,
codecs o salidas. No cualifica las grabaciones ambientales fallidas del grupo,
foros/supergrupos no recorridos realmente, nuevos proveedores, generación de
medios ni la app de iPhone aplazada. La fase A global sigue abierta y B posterior.

## Contratos incorporados

- Telegram: rutas privadas compatibles, grupos/supergrupos tipados, remitentes
  autorizados, mención o respuesta al bot y tema exacto. Toda ruta necesita prueba
  fresca; crear una ruta no acredita controlar su chat. Vinculación de grupo por
  nonce con dos revisiones, sin enviar mensajes por cuenta del dueño.
- Adjuntos de Telegram: descarga HTTPS del Bot API con límite de 5 MiB y plazo;
  importación local por sesión/hash antes de ofrecer sólo referencias al modelo.
  El análisis y la respuesta con archivo usan operaciones aprobadas diferentes.
  Enviar un archivo conserva chat/tema/mensaje de origen; no admite elegir destino
  mediante argumentos del modelo ni recordar un permiso entre ejecuciones.
- Cancelación y revocación: identidades exactas de ejecución/ruta/cliente;
  cierre retenido y ausencia de replay tras respuesta incierta. Las pruebas RAM
  de otras rutas vigentes no se pierden al enlazar otra conversación.
- Voz: elección explícita de idioma/voz local, dictado al borrador, captura
  extendida y palabra de activación dentro de una captura visible y acotada.
  No hay micrófono de fondo al iniciar la app ni envío automático del dictado.
- Conversación por voz: revisión separada, hasta tres turnos/cinco minutos;
  reconocimiento final se convierte en texto hacia Codex con ChatGPT. Micrófono
  cerrado durante modelo/lectura, pausa después del TTS y detección de posible
  eco. Aprobación pendiente, TCC, foco, suspensión o cambio de contexto detienen
  el modo. Un parcial sólo pasa al borrador por otra acción visible.
- Documentos: adjuntos inmutables por sesión/workspace/hash, temporales RAM,
  importación revisada, previsualización y retirada. La extracción acotada cubre
  texto, HTML inerte, CSV/TSV, JSON y Office XML sin macros/relaciones externas.
- Observación nativa: PDFKit, Vision OCR, imágenes, RTF, audio/vídeo en memoria;
  rangos de páginas y muestreo de vídeo declarados, citas/hash y límites. Speech
  local requiere el modelo exacto instalado; no descarga ni busca otra nube.
  Ogg/Opus se convierte localmente a WAV con relación de huellas comprobada.
- Reproducción: bytes del ámbito elegido en una fuente AVFoundation en memoria,
  sin URL de red ni archivo temporal. El usuario inicia la reproducción. Cada
  reproductor fija una salida física local admitida; no cambia la salida global
  del Mac, busca dispositivos en la red ni usa una salida virtual como respaldo.
  La retirada/cambio de contexto o salida cierra el reproductor sin reanudarlo.
  Los bytes y la huella del original se distinguen de los del WAV derivado.

Los límites y los contratos de formato están en
[Documentos y medios](PHASE_A_DOCUMENTS_MEDIA.md). La observación técnica de bytes
no comprueba la veracidad del contenido; documentos y resultados externos no
modifican instrucciones ni autorizan herramientas.

## Investigación por partes

Se estudiaron 16 blobs seleccionados del snapshot OpenClaw
`1e3ff306abaff29f03e7668d53b01c07f10294ac`, sin ejecutarlo ni incorporarlo.
[Manifiesto](REA_OC26_MANIFEST.json), [resúmenes REA](REA_OC26_SUMMARIES.json) y
[contratos de extracción](REA_OC26_MEDIA_CONTRACTS.md) conservan rutas, tamaños,
hashes, Evidence IDs e incógnitas. OC-26A informa admisión/entrega de Telegram;
26B, captura y destino de voz; 26C/D, extracción, codecs y resultados de adjuntos.
La unión del registro pasa a 45 lotes y 171 rutas TS/JS seleccionadas, deduplicadas.
La cobertura global sigue parcial: un grafo estático no acredita comportamiento.

## Cualificación que se debe acreditar

1. CI completa Ubuntu/macOS: Python, Swift, wheel/sdist, smoke y bundle del commit
   exacto. No ejecutar suites o compilaciones completas en el Mac del dueño.
2. Corpus artificial: PDF textual/escaneado/cifrado, Office, imagen con OCR, tono
   WAV/Ogg/Opus, vídeo corto; citas/huellas, rechazos y limpieza verificables.
3. App nativa del mismo producto en perfil sintético: importación, aprobación,
   análisis con Codex real, preview/reproducción, revocación y cierre.
4. Telegram elegido por el dueño: credenciales introducidas sólo en la app,
   enlace fresco, respuesta/adjuntos, cancelación y revocación. Los fixtures no
   acreditan exclusividad de consumo externa ni entrega humana del Bot API.
5. Voz en el Mac objetivo: permisos TCC del dueño, frase sintética, borrador
   conservado y conversación explícita; escucha humana o medición acústica
   separada del recibo de reproducción. Nunca inferir audibilidad de un ACK.

Las aprobaciones de los perfiles sintéticos están delegadas expresamente al
agente. Credenciales, TCC y autenticación siguen requiriendo al dueño. No se leen
sus chats de Telegram, historial de Jarvis ni credenciales de otros perfiles.
