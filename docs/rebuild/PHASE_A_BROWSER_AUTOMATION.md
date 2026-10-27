# Navegador propio de Jarvis — contrato del incremento A

El backend nativo implementa un navegador WebKit privado y la conexión a una
pestaña CDP elegida expresamente. Su entrada productiva es el broker firmado y
la autoridad única de Jarvis; no se importa ni ejecuta el runtime de OpenClaw.
El cierre integrado del bloque registra por separado los recorridos de la app,
los controles globales y la CI. Las pruebas de este documento son integración
nativa con páginas, servidores y decisiones sintéticos propios.

## Investigación por partes

[OC-22C](REA_PART22C_SUMMARY.json) conserva cuatro fuentes TypeScript y el
manifiesto de la revisión `1e3ff306abaff29f03e7668d53b01c07f10294ac`.
Se adquirieron los blobs exactos mediante `git show` en una partición nueva
privada de sólo lectura; no se modificó el árbol de consulta.
REA conservó el padre
`ev_3c7e85f98d2003f2c62bf8ddd2c7391d57822db6e0cfa4b98c74ac78a1cc2355`
y la vista resumen
`ev_54b251888f13120cddb4952931268256a959114a52b00fa81095901620c012cb`.
La semántica sigue desconocida, con 631 incógnitas. La cobertura de los cinco
archivos de esta partición no equivale a revisar el navegador completo de la
referencia, sus dependencias ni su ejecución.

Las observaciones estáticas relevantes son la vinculación del destino antes
del despacho, el tratamiento del contenido de página como datos no confiables,
la exclusión de páginas internas del navegador y la separación entre plazo de
operación y transporte. Los contratos siguientes son diseño e implementación
propios, comprobados independientemente de esas observaciones.

## Ventanas y destinos

`BrowserAutomationExecutor` crea como máximo ocho pestañas, cada una con una
ventana nativa de Jarvis y `WKWebsiteDataStore.nonPersistent()`. No busca cuentas,
perfiles o pestañas personales. Los datos web privados no se exportan mediante
el catálogo. Reset cierra las vistas propias y sus transportes; no conecta otra
cuenta ni repite operaciones anteriores.

La conexión existente requiere un endpoint loopback y un `target_id` explícitos.
Se abre únicamente `/devtools/page/<target_id>` y se valida `Target.getTargetInfo`
para ese ID y tipo `page`. No hay `Target.getTargets`, lectura de `/json/list`,
conexión al browser target, descubrimiento implícito, API de cookies ni cambio
de la política de red del navegador ajeno. Cerrar esa conexión sólo la separa;
no cierra la pestaña externa. Su red continúa bajo el control de ese navegador.

## Observación y acciones

Cada petición tiene una identidad de operación de un uso. La captura DOM usa
scripts propios fijos con parámetros JSON, en un `WKContentWorld` conservado o
un contexto CDP aislado del mundo de la página. El modelo no puede enviar código
JavaScript, selectores arbitrarios, métodos CDP ni rutas de perfil.

La vista entrega un snapshot, URL/origen, texto acotado y referencias a controles
del documento principal. Las contraseñas, campos ocultos, códigos de un uso y
campos de tarjeta con autocomplete se excluyen. Se limita el resultado a 96 KiB;
el texto y las referencias llevan `external_unverified=true`. No son
instrucciones de sistema, una autorización ni prueba de los efectos del sitio.

`expected_origin` se comprueba antes de leer y actuar. Un cambio de documento,
mutación DOM o sustitución del elemento invalida la referencia. Inmediatamente
antes de actuar, el script vuelve a comparar origen, snapshot, generación,
conexión, visibilidad y huella del elemento. El callback confiable `beforeEffect`
revalida además el lease del host después de las esperas de DOM/transporte y
antes de crear/adjuntar/navegar, modificar, descargar o cerrar. Un argumento del
modelo no puede reemplazar ese callback.

Se admiten click, fill, selección, scroll y un conjunto finito de teclas. Fill y
selección comprueban el valor observado; click y teclado se reportan como
acciones despachadas, sin convertirlas en éxito de un trámite externo. Los
KeyboardEvents de este backend son sintéticos: no afirman equivalencia universal
con entrada física ni eventos `isTrusted` de cada sitio. Un cambio de origen
requiere otra observación admitida antes de entregar su DOM al modelo.

## Red y descargas

Las páginas HTTPS del navegador propio se configuran con un proxy CONNECT
privado autenticado y `allowFailover=false`. El proxy comprueba las direcciones
DNS y abre una dirección numérica pública concreta; la verificación TLS de
hostname/certificado permanece a cargo de WebKit. Rechaza destinos privados,
ambiguos, puertos no admitidos, cabeceras excesivas y túneles fuera de presupuesto.

WebKit puede excluir HTTP loopback del proxy por diseño del sistema. Por eso
hay también una lista de reglas de contenido obligatoria: bloquea HTTP y
orígenes locales/literales, salvo los orígenes sintéticos exactos admitidos por
el host, incluido su puerto. El modelo no puede añadir excepciones. Los
recorridos QA que usan esa excepción se identifican con
`synthetic_origin_exception=true`; no se atribuye a su tráfico HTTP una fijación
DNS que no ocurrió. Las pruebas verifican tanto el bloqueo de un subrecurso
privado no admitido como el paso de HTTPS por el proxy autenticado antes de la
denegación DNS, sin visitar un sitio externo.

Las descargas WebKit automáticas y el selector de subida de archivos se
rechazan. `browser_download` exige una pestaña vigente y el mismo origen del
recurso antes de hacer una lectura explícita, sin cookies ni
credenciales ambientales, sin redirects y con un máximo de 64 KiB. Devuelve
bytes en memoria, nombre saneado, tipo y SHA-256; no escribe archivos. La
publicación en un destino nuevo del workspace y su comprobación independiente
pertenecen a la operación autorizada del host. No se copia la sesión autenticada
de otra pestaña para descargar.

La búsqueda requiere una plantilla de URL elegida en el control del dueño,
con exactamente un marcador `{query}`. El broker la fija; el modelo sólo
propone el texto. La cualificación usa un buscador HTTP sintético propio. No se
elige silenciosamente un motor externo ni otro proveedor de inferencia.

## Propiedad y fallos

La identidad se consume antes del despacho, incluso si se pierde la respuesta;
reset no vuelve a habilitarla. No se reenvía una mutación por timeout. Cancelar
invalida la observación, detiene la carga propia y cierra el transporte CDP o de
descarga; un efecto que pudo despacharse queda incierto. En WebKit no se resuelve
una evaluación pendiente sólo por `stopLoading`, plazo agotado o cierre de la
ventana: se conserva la custodia hasta el callback real o la notificación de
terminación del proceso WebContent. Reset también espera esa evidencia. Si el
script no termina, Jarvis conserva el bloqueo en vez de acreditar una limpieza
que no ocurrió. El host conserva su recibo durable y decide el asentamiento,
sin deducirlo del texto del modelo.

Desconectar CDP termina la conexión de Jarvis; no cancela el código ni revierte
un efecto que el sitio ya recibió. Tampoco se cierra una pestaña externa para
simular esa garantía. Las acciones inciertas no se repiten automáticamente y
una cancelación nunca se presenta como reversión de un trámite del sitio.

Los diálogos JavaScript de confirmación se presentan en una hoja de la ventana
propia para decisión humana; no se autoaceptan. Los diálogos de texto, uploads,
popups y permisos de cámara/micrófono no reciben concesiones automáticas.
No se omiten advertencias TLS ni se leen secretos para resolver una prueba.

## Verificación y límites

Los casos propios usan WebKit real con páginas locales, un servidor HTTP de
prueba y un servidor WebSocket CDP propio. Comprueban contenido y entrada literal,
selección/click, refs obsoletas, origen discordante antes de leer o navegar,
historia, búsqueda, descarga por bytes, cancelación, revocación del lease justo
antes del efecto, ID consumido, destino CDP ajeno rechazado, ACK perdido y
plazos finitos sin repetir. El peer CDP verifica el contrato de transporte;
su evidencia se mantiene separada de la calificación adicional con Chrome real
y de cualquier inferencia autenticada.

Se conservan los intentos iniciales: un fixture CDP contestaba HTTP/1.0 al
handshake WebSocket; se corrigió a HTTP/1.1. Otro caso detectó que recrear
`WKContentWorld` entre evaluaciones perdía el snapshot: ahora se conserva el
mismo mundo aislado durante la vida del executor, sin relajar CAS. El negativo
de red descubrió el bypass loopback del sistema y motivó las reglas obligatorias.
Los logs de esos intentos no se presentan como pruebas aprobadas. Las reglas
se expresan sin disyunciones, que la gramática de filtros WebKit no soporta.

El focal de regresión después de corregir la custodia de JavaScript pasó
**39 pruebas nativas, cero fallos, en 20,268 segundos**. Incluye nueve casos
WebKit/política, cuatro de transporte CDP, tres de auditoría de ventanas y
cancelación, y 23 del backend Mac. Log:
`/private/tmp/jarvis-mac-browser-audit-fixed-tests.log`. Los negativos de
cancelación y reset inician un handler propio que continúa 1,5 segundos:
comprueban que la operación sigue pendiente hasta el callback real, conserva
el efecto incierto y no admite repetir su identidad. El focal anterior de
13 pruebas queda como evidencia histórica, no como identidad de las fuentes
posteriores a esta corrección.

El host firmado pasó además **cuatro pruebas, cero fallos, en 3,931 segundos**:
dos del DTO/hash exacto y dos de integración host Swift → RPC firmado → servicio
Python → peer de modelo propio → WebKit real. Se verifican seis claims y
completions, revalidación previa al efecto, un POST único, bytes/hash publicados
en el workspace, reinicio sin replay y claim discordante sin despacho nativo.
Log: `/private/tmp/jarvis-native-automation-integration-second.log`.

La calificación independiente con **Google Chrome 155.0.8059.85 real** pasó
**un caso, cero fallos, en 2,945 segundos**. Se ejecutó
`BrowserAutomationChromeTests` con `JARVIS_REAL_CHROME=1`, el ejecutable
`/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`, un directorio
`user-data-dir` nuevo en `/private/tmp` y un servidor HTTP propio. El fixture
crea su destino con `/json/new`; Jarvis adjunta sólo el ID devuelto. No se
enumera `/json/list`, perfiles ni pestañas del dueño. Se comprueban lectura,
fill con valor observado, click con un POST independiente, referencias antiguas,
origen discordante, navegación y desconexión conservando el target. Una segunda
conexión CDP a ese mismo ID confirma que la pestaña sigue abierta. El cierre
del Chrome propio y del servidor se confirma por el proceso, sin forzarlos.
Log: `/private/tmp/jarvis-real-chrome-tests-final-2.log`.

Este caso es opt-in y en la suite ordinaria muestra una omisión explícita;
puede ejecutarse con
`JARVIS_REAL_CHROME=1 ./script/test_desktop.sh --filter BrowserAutomationChromeTests`.
El entorno debe permitir lanzar el navegador y enlazar el fixture loopback;
no se suprime una restricción convirtiendo el fallo en éxito. El intento
inicial quedó bloqueado al abrir el servidor en el sandbox. Los dos siguientes
recorridos de acciones llegaron al final pero fallaron el cierre del arnés:
se interrumpía la salida asíncrona de Chrome con SIGTERM. Ahora se espera el ACK
o cierre CDP acotado, se concede una gracia de salida y se comprueba `waitpid`.
La gracia por sí sola no acredita limpieza. Los logs fallidos se conservan
por separado y no forman parte de la cifra aprobada.

Los hashes de estas fuentes, pruebas y logs figuran en `own_validation` de
[OC-22C](REA_PART22C_SUMMARY.json). La suite general y el recorrido de la app
se registran separadamente en el cierre integrado del bloque.

Los filtros bloquean el subrecurso privado `http://127.0.0.1:<puerto-ajeno>/forbidden`
que una página de prueba intentó cargar; el servidor señuelo no recibió esa
petición. La prueba HTTPS llegó al proxy con su autenticación correcta antes
de fallar por el nombre reservado `.invalid`, sin abrir un destino web externo.

Fuentes de API consultadas: [WebKit WKWebView](https://developer.apple.com/documentation/webkit/wkwebview),
[datos web privados](https://developer.apple.com/documentation/webkit/wkwebsitedatastore/nonpersistent()),
[URLSessionWebSocketTask](https://developer.apple.com/documentation/foundation/urlsessionwebsockettask),
[CDP Target](https://chromedevtools.github.io/devtools-protocol/tot/Target/) y
[CDP Page](https://chromedevtools.github.io/devtools-protocol/tot/Page/), además de
las declaraciones WebKit/Network del SDK local de Apple.

Este incremento no acredita todos los marcos/cross-origin iframes, popups,
extensiones, eventos de entrada física, autenticación de sitios ni todos los
sitios web posibles. El transporte existente es una página CDP elegida; no es
un relay universal de extensiones ni defensa contra malware con el mismo usuario.
Los permisos reales, la app personal y la cuenta del dueño no se usaron en estas
pruebas. La cualificación integrada registra aparte lo observado desde SwiftUI.
