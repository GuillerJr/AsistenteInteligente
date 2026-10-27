# Observación y acciones nativas del Mac

Implementación propia para la app macOS de Jarvis. Se conecta al broker de
herramientas del host; este backend no concede aprobaciones ni sustituye la
autoridad central. La investigación [OC-22B](REA_PART22B_SUMMARY.json) contiene
dos particiones estáticas y siete fuentes TypeScript de la revisión fijada.
OpenClaw no se ejecutó ni se incorporaron su implementación o recursos.

## Lo observado en la referencia

La herramienta `screen` de la referencia controla presentación de paneles del
cliente: su despacho no acredita entrada física ni una vista funcional. La
invocación genérica de nodos rechaza comandos que pertenecen a herramientas
dedicadas. La ruta de control de computadora conserva generación, ventana y
observación exactas, con referencias opacas a elementos y geometría. Cambiar
de generación invalida esas referencias. Estos hallazgos estáticos informan
el contrato independiente siguiente; no son observaciones de ejecución.

Los padres REA son
`ev_1ce045e1d2808001122ef58726f75d575bc3e68e05939b50675135ebcf9400df`
y `ev_c52b7b4f29b8e20a45d473d7e54b24a8b964f6e719578f6a6060c1a1a8dda907`.
Las vistas conservan 271 y 331 incógnitas semánticas; la cobertura de aplicación
es parcial y la semántica permanece desconocida. Los manifiestos, blobs Git,
hashes y bundles completos están en el registro JSON. Las dependencias y el
resto del árbol de referencia no se declaran revisados.

## Destino y autoridad

`MacAutomationExecutor` recibe comandos ya autorizados del worker nativo propio.
Cada uno lleva una identidad de operación de un uso, emitida por el host y
ajena a los argumentos del modelo. El worker configura el callback confiable
`beforeEffect(operationID)`: se consulta al admitir el comando y otra vez justo
antes de las acciones AX, después de esperar la activación, antes del input CG
y durante su postcondición. Revocar o cancelar durante una espera impide el
siguiente efecto. La omisión del callback sólo sirve para tests de componentes;
el executor aislado no es una autoridad de autorización.

La selección inicial exige un bundle ID explícito. Sólo se resuelven procesos
de ese bundle. El destino fija bundle, PID y hash de identidad de lanzamiento,
incluyendo fecha de arranque y ruta del ejecutable observadas por AppKit.
La app instalada, historial o procesos ajenos no se eligen implícitamente.

Las apps de permisos del sistema, llavero, autenticación y login permanecen
reservadas al dueño: se rechazan los bundles de Ajustes del Sistema, Acceso a
Llaveros, SecurityAgent, authorizationhost y loginwindow. La comprobación se
repite al validar el destino; no se conceden permisos TCC automáticamente.

Jarvis tampoco puede controlar sus propias aprobaciones. Para su propio PID,
el driver admite únicamente ventanas cuyo identificador nativo pertenece a
`jarvis.browser.*`. Los chats, ajustes y tarjetas de autorización no se incluyen
en el inventario ni se leen sus títulos o etiquetas. El identificador lo asigna
la app; el texto del modelo y el título de una ventana no pueden convertirla
en un destino permitido. El fixture exacto `jarvis.automation.fixture` exige el
flag confiable `allowOwnTestWindow`, desactivado por defecto. El host sólo lo
habilita al arrancar el modo de pruebas local explícito.

El ciclo de vida del worker se serializa en una tarea compartida: llamadas
concurrentes a inicio, cierre o reconexión no generan registros ni retiros
duplicados. No se puede cambiar su transporte mientras se registra, está activo
o espera limpieza. Si llega un cierre mientras `attach` está pendiente, se
conserva su ACK real para retirar ese mismo host. Si llega durante una
reconexión, impide registrar el reemplazo. El panel deshabilita reconexión y
configuración mientras `lifecycleBusy` sea verdadero.

Para enlazar AX con una ventana propia se exige geometría unívoca tanto en los
registros CG del PID como en las ventanas AX, y un `CGWindowID` admitido por
AppKit. Una ventana excluida con igual geometría produce rechazo, no hereda el
permiso de la admitida. Los metadatos geométricos de la propia app sólo sirven
para descartar esa ambigüedad; no se leen los títulos excluidos. Un modal o una
sheet impiden actuar sobre la ventana propia. Esta política vuelve a comprobarse
después de cada espera de autorización, inmediatamente antes del efecto.

Las ventanas se obtienen de la aplicación seleccionada mediante AX. El ID
público de WindowServer se resuelve únicamente si una descripción de ese PID
coincide de forma unívoca con la ventana AX. Título y geometría resuelven el
vínculo; **no son su identidad**. La referencia es un hash del proceso fijado y
su `CGWindowID`, estable al reiniciar el backend mientras siga viva la misma
ventana. Otra ventana con igual título y geometría obtiene otra referencia.
Si hay ambigüedad, se rechaza. No se utiliza el atributo privado AXWindowNumber.
Se descartan los registros de otros PID antes de consultar sus títulos o bounds.

Antes de actuar se revalidan proceso, ventana AX, `CGWindowID`, observación,
contenido, selección y geometría. Los checks nativos no son una primitiva
atómica de WindowServer ni una defensa contra malware con el mismo usuario.

La primera comprobación AX real de la ventana sintética detectó un rechazo
incorrecto: AppKit añadió un `AXGroup` vacío bajo otro grupo vacío del chrome
entre observar y actuar. El documento y sus controles no cambiaron, pero el
digest del árbol completo los interpretó como contenido nuevo. Dos lecturas
consecutivas aislaron el cambio; la acción fallida declaró `dispatched=false`
y efecto conocido, por lo que no hubo un efecto incierto que repetir.

El digest semántico poda ahora sólo subárboles compuestos íntegramente por
`AXGroup` vacíos, habilitados, sin valor, etiqueta, acciones, selección ni
protección. Un descendiente con contenido o de otro rol conserva sus ancestros
y vuelve a invalidar el CAS. El cálculo tiene un límite de 160 nodos; ante
ciclos, padres ausentes o IDs duplicados conserva el árbol íntegro. Los nodos
siguen visibles y conservan sus refs; no se normalizan IDs de controles ni se
traslada una acción a un elemento AX reemplazado.

## Observación y referencias

`desktop.observe` lee sólo la ventana elegida. El inventario limita profundidad,
tiempo y elementos. Devuelve roles, etiquetas, valores acotados, selección UTF-16
y acciones soportadas. Los elementos protegidos o sin tipo AX verificable no
exponen valores, etiquetas originales ni acciones. Tampoco se recorre su árbol.

Las referencias de elemento pertenecen a una observación y una ventana. La
observación caduca a los 120 segundos, se consume al intentar actuar y una
observación nueva de esa ventana retira las anteriores. El plazo permite revisar
una aprobación sin renovar el permiso automáticamente. Reset, cancelación,
cambio de proceso/ventana o contenido obsoleto retiran las referencias. Las
ventanas vivas conservan su referencia física; no se cambian por un UUID nuevo
cada dos minutos. Hay límites de 128 bindings de ventana y 16 observaciones.

La salida reserva presupuesto conjunto para JSON, etiquetas, texto e imagen:
como máximo 160 elementos, 12.000 bytes de texto de valores y menos de 128 KiB
de resultado. La abreviación se declara y no permite referenciar controles
omitidos. El texto del escritorio es observación externa, no instrucciones ni
una autorización nueva.

## Píxeles de la ventana en memoria

La opción explícita `include_frame` usa ScreenCaptureKit sobre el `CGWindowID`
exacto; no observa todo el escritorio ni escribe un archivo. El permiso de
grabación de pantalla se comprueba sin solicitarlo automáticamente. Se exige
una vista AX completa y estable durante la adquisición. Las regiones de
controles protegidos o sin tipo verificable se cubren antes de codificar la
imagen; si no se puede determinar su geometría, se retiene la imagen completa.
No se afirma detectar secretos arbitrarios dibujados por una app sin semántica AX.

La imagen JPEG permanece en RAM y tiene un máximo de 48 KiB, con SHA-256 y tamaño.
Se excluyen cursor, audio, sombras y ventanas hijas. AX y píxeles son lecturas
separadas; `atomic_with_ax=false` evita afirmar una captura atómica. El host debe
entregar el `data_url` al modelo sólo por su canal efímero y retirar la imagen
de recibos, SQLite, historial y logs. El registro durable puede conservar hash,
tamaño y procedencia. Un reinicio no recupera la imagen ni repite una observación.

## Acciones y comprobación

- `set_value` reemplaza exactamente el texto autorizado, incluido el texto vacío.
  `type_text` inserta o reemplaza en la selección UTF-16 observada, conservando el
  resto del contenido y las nuevas líneas. Se comprueban valor y selección otra
  vez antes del efecto; no se supone que el cursor estaba al final.
- `press`, `focus` y scroll usan sólo las acciones/atributos públicos que anuncia
  el elemento AX. Una respuesta AX aceptada no es verificación semántica de una
  tarea. Releer el valor o el foco permite verificar esas postcondiciones concretas.
  Si `press` o scroll llevan `verify_ref/expected_value`, se comprueba ese estado
  AX exacto después de **un único envío**, incluso cuando el ACK nativo no pudo
  confirmarse. `native_acknowledged` conserva esa distinción. Cada relectura
  vuelve a comprobar permiso, proceso, ventana física y control no protegido.
  Una postcondición fallida, cancelada o no legible permanece incierta.
- `click_at` y `drag` usan puntos normalizados de la ventana; `key` usa una lista
  cerrada. No admite scancodes arbitrarios ni lee o pega el portapapeles del dueño.
  Requieren ventana/foco exactos y ningún modificador o botón físico presionado.
  El foco protegido o desconocido impide el input.
- Si la aprobación deja al propio host Jarvis delante, puede activar y elevar
  únicamente el destino exacto como parte de la operación autorizada. Si otra
  aplicación del dueño tomó el foco, se rechaza. Después de esperar la activación
  vuelve a consultar la autorización y las identidades antes de enviar input.
- `verify_ref` y `expected_value` permiten una postcondición explícita sobre un
  control de la misma observación. Se relee hasta ocho veces sin repetir el
  evento. Sólo una coincidencia exacta devuelve resultado comprobado. Sin esa
  prueba, CGEvent dirigido al PID sigue `verified=false/effect_status=unknown`:
  postear un evento no proporciona ACK de su efecto.

Cancelar entrega su estado pero conserva el lane mientras una callback o
comprobación central anterior siga pendiente. Un resultado tardío después de
reset/cancel no se publica ni se transforma en éxito. Los efectos despachados
sin comprobación permanecen inciertos y no se vuelven a ejecutar automáticamente.

La auditoría del navegador integrado detectó y reprodujo un fallo distinto:
`stopLoading()` podía resolver una evaluación como terminada mientras su
JavaScript despachado seguía ejecutándose y producía un efecto posterior. Se
corrigió el dueño del recurso en `BrowserAutomationExecutor`: cancelar o vencer
el plazo sólo marca la evaluación como incierta; su continuación y la página
siguen retenidas hasta el callback real de WebKit o la notificación de que su
proceso WebContent terminó. Reset y cierre esperan ese mismo recurso. Si un
script no retorna, se conserva la limpieza pendiente, sin inventar un cierre,
sin matar procesos ajenos y sin reintentar. Esto no acredita que una página
externa deje de generar toda actividad autónoma ni cualifica Chrome adjunto.

`--automation-test-origin` es una opción del cliente nativo, no una herramienta
del modelo ni un argumento reenviado al núcleo Python. Sólo se acepta con un
estado explícito bajo `/private/tmp/` y un origen exacto
`http://127.0.0.1:puerto` entre 1024 y 65535, sin path, credenciales, query o
fragmento. No se combina con recuperación ni bootstrap restaurado; abrir un
perfil restaurado elimina la excepción. Esta restricción de lanzamiento no es
una sandbox ni prueba que una carpeta indicada contenga datos sintéticos.

## Contrato nativo

Todos los comandos salvo `desktop.status` requieren `operation_id` hexadecimal
de 32 caracteres inyectado por el host. No se admiten campos extra.

| Comando | Argumentos propios |
| --- | --- |
| `desktop.status` | Ninguno. Estado de Accesibilidad/pantalla, sin elegir apps. |
| `desktop.resolve` | `bundle_id`. Devuelve destinos con `bundle_id/pid/launch_id`. |
| `desktop.windows` | `target`. Devuelve `window_ref/title` de esa aplicación. |
| `desktop.observe` | `target`, `window_ref`, `include_frame` opcional. |
| `desktop.action` | `target`, `window_ref`, `snapshot_id`, `ref`, `action`; texto `value`, puntos `point/end_point` o `key` según la acción. Postcondición opcional `verify_ref/expected_value`. |
| `desktop.forget` | Ningún argumento adicional. Retira referencias locales. |

El resultado usa `schema=jarvis.desktop.v1`, `succeeded`, `verified`,
`effect_status`, `dispatched` y `permits_replay=false`. Una operación fallida
conserva un código saneado; no devuelve stderr o datos privados del host.

## Pruebas y límites de la evidencia

El focal `MacAutomationTests` comprueba identidad y permisos antes de leer,
referencias opacas, campos protegidos, cambio de contenido/cursor/geometría,
vencimiento, no replay, lane retenido, reset, cancelación, revalidación central,
input sin ACK, postcondiciones, texto vacío, presupuesto conjunto y redacción
de regiones antes del JPEG. El ID de ventana es estable entre instancias y
rechaza ambigüedad o un ID nuevo con el mismo título/bounds.

El focal inicial pasó **22 casos con cero fallos**. Log:
`/private/tmp/jarvis-mac-automation-window-identity-tests.log`. Las selecciones
anteriores de 11, 15, 17 y 20 casos se solapan y no se suman como pruebas nuevas.
El JSON conserva sin sustituir esos hashes históricos de cuatro fuentes.

Después de la auditoría pasaron **39 casos con cero fallos** en
`/private/tmp/jarvis-mac-browser-audit-fixed-tests.log`: 23 de `MacAutomationTests`,
13 del navegador/CDP y tres regresiones de `MacBrowserAuditTests`. Las nuevas
regresiones comprueban que cancelar y reset no se asienten antes del callback
real de un JavaScript propio lento, que la operación incierta quede consumida,
y que el título de una ventana propia no conceda autoridad a sus aprobaciones,
chats o ajustes. Los 39 incluyen casos anteriores y no se suman a los 22.

La selección independiente `LaunchOptionsAutomationTests` valida el origen QA
permitido y los rechazos fuera de `/private/tmp/`, recuperación, restauración,
credenciales, otro host/esquema/puerto, flags malformados o duplicados. También
comprueba que el origen no llegue a `serviceArguments` ni se herede al abrir un
perfil restaurado. Su resultado y hashes de las fuentes actuales se registran
como evidencia nueva en `own_validation_updates` del JSON, separados del
análisis estático e identidad histórica de OpenClaw.
Pasaron **seis casos con cero fallos**, con log
`/private/tmp/jarvis-launch-options-automation-tests.log`.

El seguimiento de ciclo de vida pasó **nueve casos con cero fallos** en
`/private/tmp/jarvis-native-automation-lifecycle-tests.log`: cinco nuevos casos
con IPC firmado real y respuestas `attach/detach` demoradas, dos contratos del
payload y dos recorridos de integración nativa del broker. Se verifican los
inicios, cierres y reconexiones concurrentes, el rechazo de retarget durante
registro y la prioridad del cierre sobre un registro o reconexión pendiente.
Los dos recorridos de integración usan modelo/aprobaciones propios y un
oráculo HTTP/archivo sintético; no acreditan inferencia autenticada ni TCC.
Esta selección y sus hashes actuales quedan añadidos al JSON, sin reemplazar
los registros anteriores ni presentarse como total de la suite.

La corrección del CAS de chrome pasó **31 casos con cero fallos** en
`/private/tmp/jarvis-mac-automation-chrome-cas-tests.log`: cinco regresiones
nuevas de `MacAutomationSnapshotTests`, los 23 casos Mac y tres de auditoría.
Las regresiones distinguen una cadena de grupos vacíos de un cambio de texto,
valor, acción, seguridad, selección, habilitación o identidad física del editor.
También conservan estructuras malformadas. Se guarda una copia inmutable y
acotada de la evidencia AX sintética previa al arreglo, con hash en el JSON.
La comprobación real posterior del bundle corregido se registra en el cierre
integrado del bloque; esta selección por componentes no la sustituye.

Un seguimiento entre procesos acreditó escritura Unicode e inserción UTF-16,
pero el botón del fixture externo dejó un recibo incierto aunque su oráculo
independiente registró exactamente una subida a `CONTADOR 1`. Se identificó
que la rama AX de `press`/scroll no consumía la postcondición explícita antes de
devolver el ACK no confirmado. La corrección anterior pasó **33 casos con cero
fallos** en `/private/tmp/jarvis-mac-ax-postcondition-tests.log`: cinco nuevos
casos de `MacAutomationAXPostconditionTests`, los cinco del CAS y los 23 Mac.
El seam de despacho/relectura comprueba ACK ausente con coincidencia, ausencia
de coincidencia, errores, revocación y exactamente un envío. El timeout AX no
se modificó ni se atribuyó al ACK una causa que no fue registrada.

El recibo anterior se conserva incierto. La prueba real del arreglo exige
una operación nueva y autorizada desde el estado observado `CONTADOR 1` hasta
`CONTADOR 2`. Verificar el valor posterior acredita únicamente ese estado,
sin afirmar causalidad, cierre de todos los handlers remotos ni ausencia de
actividad autónoma posterior. La [documentación de AXUIElementPerformAction](https://developer.apple.com/documentation/applicationservices/1462091-axuielementperformaction)
explica que un error de mensajería puede coexistir con una acción realizada;
Jarvis nunca toma esa posibilidad como motivo para repetir el envío.

Un NSTextView propio real de AppKit verifica conservación de Unicode, selección
y nuevas líneas dentro del contrato del executor. Su driver de pruebas es
sintético: **no acredita AX/TCC entre procesos ni input físico real**. Los tests
de composición de píxeles operan sobre imágenes propias creadas en RAM. El
recorrido GUI integrado y la autorización TCC, cuando esté disponible, deben
registrarse aparte en el cierre del bloque. El agente nunca concede TCC.

Se usaron APIs públicas documentadas: [AXUIElement](https://developer.apple.com/documentation/applicationservices/1462085-axuielementcopyattributevalue),
[descripciones de ventanas](https://developer.apple.com/documentation/coregraphics/cgwindowlistcopywindowinfo(_:_:)),
[claves públicas de identidad](https://developer.apple.com/documentation/coregraphics/required-window-list-keys),
[filtro de ventana ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit/sccontentfilter/init(desktopindependentwindow:)),
[configuración de captura](https://developer.apple.com/documentation/screencapturekit/scstreamconfiguration)
y [CGEvent](https://developer.apple.com/documentation/coregraphics/cgevent).
La política propia usa [NSWindow.windowNumber](https://developer.apple.com/documentation/appkit/nswindow/windownumber),
[NSApplication.modalWindow](https://developer.apple.com/documentation/appkit/nsapplication/modalwindow)
y [NSWindow.attachedSheet](https://developer.apple.com/documentation/appkit/nswindow/attachedsheet).
No se traslada código de esos ejemplos ni de la referencia al producto.
