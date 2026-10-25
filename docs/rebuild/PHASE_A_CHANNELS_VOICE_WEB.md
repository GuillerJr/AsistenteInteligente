# Fase A: Telegram, voz local, web y cierre de recursos

**Continuación de desarrollo, 24-10-2026. La fase A permanece abierta.**

Este registro describe código propio y sus límites. No acredita revisión completa
de OpenClaw, funcionamiento con todas las cuentas o dispositivos, distribución
autocontenida ni sustitución de la app anterior. Las mejoras de la fase B siguen
después de acreditar la base A.

El destino inicial elegido es macOS Apple Silicon. El dueño aplazó expresamente
iPhone y eligió Telegram como primer canal externo. Esa decisión no equivale a
soporte móvil ni elimina las demás capacidades pendientes de la matriz.

## Estado de la cualificación

El [registro estructurado](PHASE_A_CHANNELS_VOICE_WEB_QUALIFICATION.json) separa
snapshots anteriores, cambios posteriores y recorridos pendientes. No transforma
un control local de desarrollo en distribución o paridad cualificadas.

| Evidencia | Estado registrado |
| --- | --- |
| Snapshot Python integrado anterior | 1230 pruebas aprobadas en 69,02 s. Es anterior a los ajustes posteriores de supervisión nativa y cierre web. |
| Python posterior al ajuste web | 1234 pruebas aprobadas fuera del sandbox, con salida 0. |
| Swift posterior a los ajustes de arranque y chat externo | 97 pruebas aprobadas en 18,015 s, con salida 0. |
| Snapshot Swift anterior | 91 pruebas aprobadas en 17,555 s. La nueva revisión nativa necesita repetir controles. |
| Ejecutable de desarrollo anterior | 6.282.048 bytes; construcción de 48,37 s; SHA-256 `8479ad560ce48f8bb15efa855ef7b361ae6c459fb336cf3f5fb5c13830302175` del archivo `Contents/MacOS/Jarvis`, no del bundle completo. |
| Ejecutable posterior a las correcciones | 6.414.608 bytes; construcción de 32,65 s; SHA-256 `d588d680927cfe4d53b9235dc8b323a25fddf38858c7449a6d5f211981cf4857` del ejecutable; plist y firma ad hoc estricta comprobados. |
| Firma y dependencias | Ad hoc, con Python/Codex externos. No es una distribución autocontenida ni reemplaza la app anterior. |
| Arranque del bundle corregido | Conexión con ChatGPT y tres chats sintéticos restaurados sin pulsar Reconectar. Ruta de Telegram conservada; bot desconectado hasta decisión explícita. |
| App nueva observada | Tras Reconectar, la interfaz mostró Codex con ChatGPT conectado, dos conversaciones sintéticas restauradas, herramientas y panel Telegram. No se atribuye una causa interna a la demora inicial. |
| Telegram | Conexión y ambas revisiones de vinculación observadas; una ruta activa en el checkpoint de la suma. Una suma de prueba recibió 4 en el chat dedicado, con journal completed y salida confirmada; sin herramientas. Pendiente el resto de recorridos del canal. |
| Voz y web reales | HTTPS público observado con recibo externo no verificado; TTS volvió a idle sin error visible. Dictado legacy es-ES falló localmente; motor moderno implementado, prueba real pendiente. |
| Cualificación final del árbol posterior | Checkpoint1234 Python/97 Swift y bundle de desarrollo registrados. Correcciones adicionales de lifecycle, fixtures y motor moderno en validación separada; no calificadas por ese checkpoint. |
| CI | El primer bloque publicado pasó Linux; macOS tuvo siete fallos relacionados con el runtime Python framework. Se integraron doce regresiones del ajuste; un CI nuevo sigue pendiente. |

El diario propio de Telegram observado tras vincular tenía modo 0600, una ruta y
un único evento de control ignorado, sin run. La inspección de metadatos no halló
campos de secreto, cuerpo o URI. El control de Iniciar no llegó al chat de Jarvis;
no se guardan los IDs reales. Después se observó una solicitud sintética de 2+2 y la
respuesta correcta 4, con salida confirmada y sin herramientas. La interfaz del
chat seleccionado no refrescó los mensajes externos hasta cambiar selección:
se corrige ese refresh de sólo lectura y su validación posterior sigue pendiente.
Después el dueño desconectó el bot; el daemon anterior terminó y su socket fue
retirado antes de cargar el reemplazo. Es evidencia del ejecutable previo al fix.
No se inspeccionó el cliente Telegram ni se probaron aprobaciones de herramientas
con este recorrido. El registro estructurado conserva sólo metadatos
desidentificados y el hash de su captura propia, sin token, nonce o IDs reales.

Como antecedente separado, una copia privada de desarrollo aprobó 128 pruebas de
Telegram, vinculación y presupuesto temporal con RPC/HTTP locales y cuentas/modelo
sintéticos. Ese resultado pertenece a su snapshot privado y no es una conversación
real con Telegram. No se guardan aquí identidad real del bot, destino, token,
nonce ni texto privado de los recorridos.

La continuación incorpora supervisión de sólo lectura tras
un bootstrap fallido, refresh de mensajes externos del chat seleccionado y cierre
web antes de despachar DNS/TLS/HTTP tardío. Estos ajustes no introducen relanzamiento automático, cambios de proveedor o ampliación de plazos;
sus regresiones pasaron en el árbol corregido; falta el recorrido posterior en la app.
El ajuste web aprobó 156 pruebas acotadas en 1,37 s, con cuatro regresiones
adicionales de cierre/cancelación. Después pasaron las 1234 pruebas integrales de Python
fuera del sandbox, sin relajar el aislamiento del producto. También pasaron las
97 nativas del árbol corregido. El bundle posterior también se construyó y validó;
los recorridos reales posteriores siguen pendientes.

## Comprobaciones posteriores del checkpoint de escritorio

El ejecutable `d588d680927cfe4d53b9235dc8b323a25fddf38858c7449a6d5f211981cf4857`
mostró una propuesta `workspace_write` llegada desde Telegram en el chat abierto,
sin recarga del agente. El dueño confirmó la aprobación. El archivo sintético
`fase-a-telegram.txt` contiene exactamente 20 bytes, sin salto final; el SHA-256
`a3f52c66d7474b34b1282c3151236336ea2d9fb8b7f8ddc9db8ccb576d702bb8`
coincide con el recibo separado verificado. También se observó el resultado sin
recargar el chat. No acredita grupos, otros chats, permisos persistentes ni revocación.

En un chat temporal, una aprobación web caducada quedó cancelada y asentada. Una
petición posterior de texto no propuso herramienta; no equivale a una tarjeta.
Tras pedir una propuesta explícita, `web_fetch` completó `https://example.com/`
con HTTP 200, 577 bytes, sin redirecciones, JavaScript ni navegación de enlaces.
La interfaz conservó `external_untrusted` y `verified=false`; el modelo reportó
«Example Domain». La vista del recibo era explícitamente parcial; no se presenta
el título como comprobación independiente. Tampoco acredita búsqueda o navegación general.

El dictado legacy rechazó `es-ES` antes de capturar: el borrador base quedó intacto
y no se envió mensaje. Un diagnóstico de sólo lectura fuera del sandbox comprobó
que faltaba el recurso legacy de ese idioma, pero el modelo moderno `es-ES` de
SpeechTranscriber ya estaba soportado e instalado (host macOS 27.0.1/SDK 27).
La corrección usa SpeechAnalyzer en macOS 26 o posterior, con readiness cancelable y sólo ese idioma
exacto instalado; no descarga recursos ni cambia de dialecto/proveedor. Sus tipos
y pruebas de contrato no sustituyen la transcripción con micrófono pendiente.
El dueño completó la lectura de una frase sintética y la interfaz volvió a idle
sin error visible; no hubo medición acústica independiente.

Después de los casos exitosos de Telegram se observó el canal detenido con un
error genérico; el último evento del diario seguía completado sin error. Su causa
no está establecida. El panel añade mensajes seguros de una whitelist para poder
distinguir fallos futuros de red, conflicto, respuesta y cierre sin revelar cuerpos
remotos ni credenciales; no introduce reconexiones automáticas.

La segunda publicación de este checkpoint se detuvo en el teardown de XCTest:
`Process.waitUntilExit` esperaba indefinidamente aunque su fixture ya no era hijo.
Se muestreó sólo ese test y se terminó para reparar la limpieza. Siete clases
usan ahora teardown async acotado que falla si fuerza o no confirma cierre.
La revisión del supervisor detectó además pérdida del dueño y reentrancia de
shutdown/reconnect; el cierre mantiene identidad y aviso hasta observar salida.
La primera suite conjunta posterior aprobó 123 pruebas en 17,933 s. La revisión
adicional del adapter ante cache de Foundation obsoleto y la preparación moderna
posterior aprobaron después 129 pruebas integradas en 18,497 s. El bundle final
se construyó y validó aparte; el dictado con micrófono sigue pendiente.

## Contrato de Telegram

Los módulos propios son `telegram.py`, `telegram_pairing.py` y
`telegram_management.py`. La app controla su configuración por RPC local; estas
mutaciones no se ofrecen como herramientas al modelo.

- El token entra sólo por una acción explícita del dueño y permanece en RAM.
  No hay descubrimiento de credenciales, lectura de variables para conectarse,
  reconexión automática, cuenta alternativa ni persistencia backend del token.
- Conectar verifica identidad del bot y ausencia de webhook por la API oficial.
  No inicia el lector. Un webhook o conflicto detiene el flujo; Jarvis no ejecuta
  `deleteWebhook` ni descarta pendientes para apropiarse del bot.
- Cada ruta es un chat privado elegido, un agente y una conversación durable
  **nueva de Jarvis**. No importa conversaciones de Telegram ni historial ajeno.
  Se aceptan mensajes privados cuyo remitente humano coincide con el chat
  permitido; grupos, otros remitentes y adjuntos no llegan al modelo.
- Iniciar requiere revisión del bot, todas las rutas y la carpeta seleccionada,
  ligada a un hash. El lector fija la identidad física del workspace; no permite
  cambiarlo mientras conserva su lease. Antes de despachar se comprueba la ruta,
  sesión, agente, configuración y carpeta vigentes.
- La entrada usa el mismo Runtime y la misma autoridad que el chat nativo.
  Un mensaje de Telegram, incluido un texto que pretenda aprobar una herramienta,
  no decide tarjetas ni concede permisos. El dueño decide en la app nativa.
- La respuesta sólo sale al mismo chat y en referencia al mensaje de entrada,
  después de un recibo durable conocido del run. No se envían avisos espontáneos.
  Se distingue texto del modelo de recibos y verificación de efectos.

El transporte usa HTTPS/TLS verificado contra `api.telegram.org`, sin proxy
implícito ni redirección a otro host. Tiene límites de cuerpo, lote y tiempo.
`connect` conserva un techo total de 20 segundos para identidad y webhook, no
20 segundos por cada paso acumulados. El preflight de vinculación tiene también
20 segundos totales; no espera a que el dueño actúe dentro del RPC. El RPC local
de control tiene un presupuesto de 30 segundos. La cancelación del cierre HTTP
se conserva, aborta la conexión propia y no habilita un segundo envío tardío.

| Límite o garantía | Alcance real |
| --- | --- |
| Rutas | Hasta 32 chats privados configurados explícitamente. |
| Texto de entrada | Hasta 8000 bytes; no se descarga ni simula contenido adjunto. |
| Respuesta | Un envío de hasta 4096 unidades UTF-16, con aviso si se trunca. |
| Diario | Hasta 2048 eventos mínimos; sin cuerpos ni token y sin expulsión automática. |
| Tombstones de vinculación | Hasta 256 pares de IDs físicos chat/mensaje, sin nonce ni texto; no se expulsan automáticamente. |
| Una instancia local | Lease exclusiva del diario; no acredita exclusividad de otro software externo. |
| Reinicio o resultado incierto | Se conserva la incertidumbre; no se repite automáticamente entrada interrumpida ni envío de aceptación desconocida. |
| Identidad de eventos | Epoch más update ID; no se confunde el reinicio aleatorio del contador de Telegram con permiso para repetir una acción. |

El diario privado aplica los permisos, validación y actualización transaccional
del núcleo. Un límite, corrupción o identidad de archivo insegura falla de forma
controlada; no elimina evidencias para aparentar disponibilidad.

## Vinculación opcional sin conocer previamente el ID del chat

Hay **dos revisiones distintas del dueño**. La primera autoriza consultar updates;
la segunda autoriza crear una ruta hacia el chat propuesto. Abrir un enlace o
recibir un mensaje no crea por sí solo ruta, sesión autorizada ni permiso de tool.

1. El dueño revisa bot, agente y avisos. Debe confirmar que el bot no tiene otro
   consumidor y que avanzar `getUpdates` confirma también pendientes ignorados.
   Esa consulta puede afectar a otra app que utilice el mismo bot. La lease sólo
   evita otro lector local del mismo perfil, no verifica exclusividad externa.
2. Jarvis crea en RAM un nonce aleatorio de 32 bytes, un challenge ID independiente
   y una vigencia absoluta de 180 segundos. Sólo un método específico de apertura
   entrega una vez la URI oficial `tg://resolve`; se abre directamente en la app
   Telegram, sin navegador alternativo, portapapeles, valor AX ni enlace visible.
3. El dueño pulsa Iniciar en Telegram. El lector sólo acepta un mensaje nuevo,
   privado, humano, con remitente igual al chat y texto de control exacto.
   No responde ni ejecuta inferencia. Los demás cuerpos no se conservan.
4. Antes de presentar el claim se persiste únicamente el tombstone de IDs físicos
   del mensaje. El claim en RAM presenta el chat candidato y queda ligado al bot,
   agente, revisión, challenge y vigencia. Aún no existe una ruta.
5. El dueño confirma el claim en la app. Se consume antes de escribir una sesión
   durable propia y su ruta. Confirmar no inicia el canal. Cancelar, desconectar o
   vencer el plazo invalida lector y claim; una escritura incierta no permite
   repetir automáticamente la confirmación.

El nonce, el token y la URI sensible no van al modelo, prompts, historial de
Jarvis, diario o registros. **Telegram conserva el propio mensaje de Iniciar**;
Jarvis no promete borrarlo. Los comandos `/start` se tratan como control y no
como chat, también si llegan después de cancelar o vencer. El tombstone físico
impide procesar otra vez el mensaje vinculado aunque cambien epoch, update ID,
offset o cuerpo. Si Telegram no está instalado, la apertura falla y se cancela;
no se abre una página como respaldo.

Fuentes primarias del protocolo: [Bot API](https://core.telegram.org/bots/api),
[enlaces de bots](https://core.telegram.org/api/links#bot-links).

## Voz local de macOS

`MacLocalVoiceDriver` usa los SDK del sistema, con control de ciclo de vida en
`LocalVoiceController` y entrega al borrador mediante `LocalVoiceDraftBridge`.
No solicita permisos, captura ni inicia reproducción al construir el controlador.

- El dictado se inicia por una acción explícita. El dueño concede o rechaza TCC
  de reconocimiento y micrófono; el agente no decide esos avisos.
- STT exige disponibilidad de reconocimiento **en el dispositivo** y fija esa
  condición en la petición. Si no existe para el idioma/Mac, informa del fallo.
  No deriva audio a nube, NVIDIA ni otro proveedor, ni descarga un motor oculto.
- El texto reconocido queda marcado para revisión y sólo puede añadirse al
  borrador de la conversación de origen todavía seleccionada y elegible.
  Conserva el borrador existente; no envía el mensaje ni aprueba herramientas.
- TTS admite voces Apple locales instaladas de los espacios de nombres permitidos
  y excluye Personal Voice. Requiere una acción explícita para leer el texto.
- Cambiar de contexto, cancelar o cerrar la superficie invalida callbacks tardíos
  y libera captura/reproducción. No hay wakeword ni escucha permanente.

Límites actuales: dictado de hasta 30 segundos, texto de hasta 16000 bytes,
finalización acotada a 3 segundos y lectura de hasta 120 segundos. La espera de
permisos también está acotada. Las pruebas con driver sintético no demuestran
disponibilidad acústica, idioma, voz instalada o permisos reales en otro Mac.
Esto no implementa llamadas, conversación de voz continua ni la cadena histórica
de audio de Jarvis; esas capacidades siguen registradas según su alcance.

## Web pública por URL explícita

`web_fetch` es una herramienta propia de lectura HTTPS con aprobación central.
No es un navegador ni un proveedor de búsqueda. Su salida es contenido externo
sin verificación de verdad; una cita y hash identifican el cuerpo recibido, no
certifican sus afirmaciones.

- Sólo URL pública HTTPS en puerto 443, sin credenciales embebidas, cookies,
  archivos descargados ni autenticación heredada. No ejecuta JavaScript, no usa
  buscador, no envía inferencia a un proveedor adicional y no consulta recursos
  enlazados automáticamente.
- Valida todas las direcciones DNS y bloquea destinos privados, de loopback,
  locales, de metadatos y rangos especiales. Fija una dirección numérica y
  mantiene el hostname original para TLS; no vuelve a resolver en cada salto.
- Hasta tres redirecciones del mismo origen bajo la operación aprobada. Otro
  origen requiere nueva revisión; no se sigue mediante permiso implícito.
- Techo total de 20 segundos, cuerpo recibido/decodificado de hasta 2 MiB y salida
  de hasta 24000 bytes. Framing ambiguo, tipos no admitidos, gzip inválido o
  límites excedidos fallan de forma explícita. La extracción es estática y acotada.
- La autoridad revalida sesión, run y argumentos inmediatamente antes de cada
  envío y antes de entregar datos. Revocar o cancelar no produce reintentos.
  Un resultado no confirmado tras enviar conserva la incertidumbre correspondiente.

La revisión REA de web está registrada como parte 12, con fuentes, hashes y
vistas finas en `REA_PART12_*`. Es estática y acotada: no acredita el transporte
HTTP completo de la referencia, los proveedores importados ni su ejecución real.

## Presupuesto de herramientas y cierre

Con workspace disponible, el catálogo base tiene 13 herramientas, incluida
`web_fetch`. El registro del servicio está configurado con hasta 48 herramientas MCP, para un máximo
configurado de **61 = 13 + 48**, dentro del límite de 64 DynamicTools de Codex.
Esto es un presupuesto de catálogo; no significa que 61 efectos o todos los
servidores MCP estén cualificados. Telegram y su vinculación son gestión del
dueño; no incrementan ese catálogo ni dan al modelo acceso al token.

Codex continúa usando la conexión oficial elegida y su entorno propio. Sus tools
builtin de efecto no sustituyen la autoridad de Jarvis. Ante cierre denegado no
se afirma que matar o reapear sólo el padre haya terminado todo el grupo: la
ausencia del grupo seleccionado debe comprobarse. Si falla señal, espera o
comprobación, conserva propietarios y estado incompleto, sin reconexión o replay.

El bloque publicado `083729885258c355f855963f09d4d59cdacf2dc4` incorporó siete
regresiones MCP: denegación de TERM/KILL, probe tras EOF, barrido final, petición
pendiente liberada sin repetir efecto, cierre de los otros servidores y
conservación del cliente incompleto al fallar la conexión. Los fixtures son
procesos propios sin fork; no acreditan árboles arbitrarios ni compatibilidad
Linux por extrapolación.

La continuación intenta cerrar propietarios independientes aunque fallen Codex,
MCP o Telegram. Mantiene Stores abiertos mientras haya cierre no confirmado y
permite un nuevo cierre explícito, sin reabrir IPC ni inferencia. Se corrigieron
también el retry del scheduler y la retención de Stores cuando el cierre RPC
vence con un handler pendiente. La cualificación del snapshot integrado anterior está registrada arriba;
la revisión local posterior pasó 1234 pruebas Python y 97 nativas;
**los recorridos reales posteriores a las correcciones y la paridad siguen pendientes**.

## Investigación y pasos que siguen

Las partes REA 10, 11 y 12 amplían la cobertura a doce lotes estáticos y
48 rutas TypeScript/JavaScript únicas seleccionadas, incluidas fuentes de prueba.
El recuento excluye metadatos de paquete/licencia y se calcula por unión de rutas;
no es revisión semántica exhaustiva ni ejecución de OpenClaw.

| Parte | Alcance seleccionado | Cobertura declarada y límite |
| --- | --- | --- |
| OC-10 | Cuatro fuentes Telegram de API, runtime, sesión y offset | Aplicación parcial: un archivo inventariado fuera de selección. Semántica unknown, 124 incógnitas. |
| OC-11 | Cuatro fuentes Telegram de autorización/pipeline, conversación y persistencia de offset | Aplicación parcial: un archivo inventariado fuera de selección. Semántica unknown, 837 incógnitas. |
| OC-12 | Cuatro fuentes web/SSRF y manifiesto | Aplicación complete sólo dentro del artefacto preparado; semántica unknown, 791 incógnitas. |

[REA_COVERAGE.json](REA_COVERAGE.json) conserva selección y Evidence IDs; los
archivos `REA_PART10_*`, `REA_PART11_*` y `REA_PART12_*` contienen resumen,
procedencia y hashes, sin trasladar código raw de referencia al producto. Las
vistas finas de OC-12 y su cierre target-free están registrados con sus límites;
el fallo de readiness de Ghidra no participó en ese análisis JavaScript.

Siguen pendientes los recorridos reales, permisos y decisiones del dueño, la
identidad final de artefactos tras los ajustes y la cualificación de distribución.
Un error o dependencia se conserva en el registro. No se marca una familia completa
por compilar o por aprobar fixtures; la fase B todavía no empieza.

El bundle moderno final (`c7e6a34d07e136958c803884fd0d7339770998e52ac4512d8087c4a1850e64c3`,
6.706.384 bytes, 29,60 s de construcción, firma ad hoc estricta) inició sin
reconexión manual. El dueño dictó una frase: se observó «BORRADOR BASE» seguido de
«Prueba de voz local 42.» en el campo de edición, con aviso de revisión y sin
mensaje enviado. Es un caso corto de micrófono a borrador en este Mac, no prueba
de todos los idiomas, escucha permanente o la matriz completa de TCC. La app
anterior no fue reemplazada y el nuevo bundle sigue usando Python/Codex externos.
