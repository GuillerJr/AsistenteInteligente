# Producto, sesiones y coordinación avanzadas

La corrección del cliente `c41378a` se publicó en `main`. Ubuntu aprobó 3980
pruebas (47 omitidas, 224,16 s); macOS terminó sin runner ARM64 en dos intentos,
por falta de capacidad del proveedor. No se ejecutaron compilación ni pruebas de
macOS para esa revisión. La
[CI actual](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/41852070764)
no está aprobada. La [plantilla G](qa/product-background-native-20261104-g-template/SNAPSHOT.json)
conserva el recorrido preparado y su observador firmado, sin ejecución. Antes
de usarla deben fijarse de nuevo el SHA de `main`, CI completa, artefacto y pines
exactos. No hay app ni cuota de prueba activas. Este bloqueo no convierte los
recorridos parciales en cualificados ni inicia B.

Continuación solicitada el 30-10-2026. La implementación está conectada y la
[CI completa de `2c8259de`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/41842491175)
aprobó 3980 casos Python en Ubuntu (47 omitidos), 4027 en macOS y 710 Swift
(una omisión, cero fallos), paquetes, bundle/helper, exportador nativo y smoke
de cierre por EOF. Los 25 casos nuevos del exportador pasaron en ambas plataformas.
Los recorridos finitos de clientes, Git, GitHub, preguntas, carpetas y paneles
conservan evidencia separada en el
[registro de cualificación](PHASE_A_PRODUCT_COORDINATION_QUALIFICATION.json).

La QA de octubre del artefacto `1b0a7ead` acredita la navegación por un clic físico
OS confirmado por el dueño. Con B seleccionado, el clic volvió a A: la interfaz
mostró «Prueba de aviso terminada», el control de avisos en 1 y «Chat abierto».
El mismo aviso pasó de `delivered` revisión 3 a `resumed` revisión 4. Hubo un
único run de Codex `completed`, resultado conocido, limpieza confirmada y cero
herramientas; B quedó vacío, con avisos desactivados y revisión 0.

El intento sigue parcial respecto de su plan de seis etapas: un índice AX
inválido y la respuesta rápida impidieron observar B mientras A seguía activo.
La navegación OS está cualificada en ese caso; el recorrido UI completo no.
El nuevo caso independiente E tampoco alcanzó esa etapa: su único run real
terminó sin llamar a la pregunta canónica ni a otra herramienta. Se observaron
las preferencias A/B antes y después. F observó B mientras A seguía activo,
pero se detuvo por el sublímite del proveedor antes de completar el recorrido.
Se conserva parcial y no se repitió su run. El bloque no se
presenta como completo: la matriz mantiene 62 familias parciales, 18 sin
implementar y ninguna familia amplia completa. La fase A sigue abierta y B
aplazada; la publicación real de GitHub continúa sin ejercitarse.

El cierre de esta QA fue normal, sin señales: cuatro identidades conocidas
ausentes, el mismo lease libre y ambas cuotas desmontadas normalmente al primer
intento. El recibo de cierre conserva SHA-256
`8fd28daeb775a09fdc7cefa4989a368cc3cde9fc34230fefa56d8704f79578fe`.

El intento anterior de `4686f25` se conserva con su explicación: el dueño
confirmó que pulsó «Ver chat» dentro de Jarvis, gesto que consume la notificación
y retira su solicitud del sistema. Su ausencia posterior no demuestra un fallo
de envío; aquel ACK y `resumed` tampoco acreditaban por sí solos un clic OS.
El nuevo caso con clic físico no reescribe ese registro histórico.

La continuación cambia la etiqueta de `delivered` a «Aceptado por macOS» y
explica su límite en la propia fila. El adaptador inmutable usado por los
controles fija identidad, revisión y ambos valores al construirlos. Así un clic
antiguo no puede tomar la revisión actual y sobrescribir una preferencia nueva
del mismo chat. Una revisión obsoleta no envía RPC ni modifica el snapshot y
pide revisar los avisos actuales; un contexto que ya cambió se descarta sin
escribir errores en el nuevo chat. Las pruebas del adaptador y Store reales usan
barreras para A → B → respuesta A demorada → B → A, rechazo de contexto/revisión
y CAS vigente. Pasaron la CI remota exacta de `1b0a7ead`. Un valor AX de
preferencias que no coincidió con el snapshot RPC de B se conserva como
observación sin causa establecida, no como una fuga de permisos demostrada.

La primera CI de esa continuación (`3c35fa0`, ejecución `41115001544`) aprobó
3952 casos Python en Ubuntu (47 omitidos), 3999 en macOS, bundle y paquetes.
Swift ejecutó 708 casos con una omisión y dos fallos del único caso nuevo de
render, uno inesperado. Los seis casos del núcleo de avisos y los tres de
apariencia pasaron. El oráculo AX no informó el estado ni la etapa que no
coincidían; ese registro no acredita una fuga de preferencias ni una causa de la
ausencia del aviso. La segunda CI (`c403ba7`, ejecución `41118849186`) aprobó de
nuevo Python, bundle y paquetes; Swift ejecutó 708 casos, una omisión y un fallo.
El diagnóstico ahora identifica A inicial: 47 nodos, ningún control ni texto del
panel, ventana visible pero no principal, sin mutaciones. Nunca alcanzó B.

Ese extractor no permite evaluar la UI y se retira de las pruebas activas; su
código y ambos fallos permanecen en Git y en el registro. Se sustituye por los
contratos deterministas de inputs/Store, sin atribuirles cobertura de render.
La UI A → B → B tras completar A → A y el texto de aceptación siguen siendo
requisitos obligatorios del recorrido externo con CUA. E observó los valores
de A y B antes y después, pero no B durante A. El clic OS ya está
acreditado en la QA de octubre; aún falta esa observación durante el run. Ni un
snapshot RPC ni una representación AX sintética sustituyen esa evidencia.
No se ejecutaron suites ni compilaciones locales.
CI conservará además el log ya acotado de Swift inmediatamente después de esa
suite, antes de Python, con `pipefail` y el código de salida original; las pruebas
del fragmento YAML verifican fallo 37, éxito 0, salida y archivo exactos.
Los [resultados vigentes de CI](https://github.com/GuillerJr/AsistenteInteligente/actions/workflows/ci.yml?query=branch%3Amain)
deben contrastarse con el SHA exacto del código y del artefacto. La CI aprobada
de `1b0a7ead` no transforma los fallos de `3c35fa0` y `c403ba7` en éxitos.

## Reanudación del 4 de noviembre

La CI del mismo código `1b0a7ead`, intento 2, volvió a aprobar ambas plataformas:
3955 Python en Ubuntu (47 omisiones), 4002 en macOS y 710 Swift (una omisión,
cero fallos), paquetes y bundle autocontenido. Las referencias temporales de
octubre ya no conservan sus archivos originales; el registro distingue esa
disponibilidad actual de la custodia que se verificó al cerrar aquel recorrido.

Tres preparaciones nuevas terminaron antes de abrir Jarvis. La primera detectó
un recorrido de 2642 directorios repetido por bloque de descarga; las siguientes
conservaron sus límites y fallaron al transferir el ZIP de 299 MB, tanto por la
vía directa como por la referencia del conector. Cada cuota se desmontó
normalmente al primer intento, conservando su imagen y los recibos saneados.
No hubo inferencia ni se modificó la app instalada. Los códigos exactos y sus
límites están en el registro; no se atribuye una causa de red no establecida.

Para este único caso UI se añade un artefacto nativo de desarrollo separado,
producido desde el bundle ya compilado en CI. Exporta el ejecutable, el
lanzador, Info.plist y su sello nuevo; conserva las secciones de código y los
bytes del lanzador. Excluye runtime, configuración, perfiles y recursos ajenos.
Tiene manifiesto propio, límites de 32 MiB comprimidos y 128 MiB expandidos y
retención de 14 días. La CI exacta `2c8259de` aprobó el exportador y sus 25
casos nuevos; generó el artefacto nativo de 7.698.129 bytes que usaron D y E.
Pasaron también las suites completas, el bundle autocontenido y sus paquetes.
Estos resultados remotos no acreditan el recorrido UI pendiente.

La QA de desarrollo usó Python 3.11.15, fuentes seleccionadas y dependencias
locales copiadas a una cuota nueva, junto con Codex oficial 0.163.0. Falta
`soundfile` en ese entorno local y no se ejercitará audio: su importación es
diferida al decodificar Ogg. Esta selección verifica preguntas y aislamiento
de la interfaz; no cualifica distribución autocontenida, códecs ni todas las
capacidades. Cada proveedor fue nuevo y privado; el dueño completó el acceso
oficial de E.

La preparación D terminó en 45,472 s, pero el observador falló al confirmar
metadatos de proceso antes de admitir el caso. La recuperación posterior por
GUI cerró sus tres identidades conocidas y desmontó la cuota normalmente, sin
señales; no hubo login, modelo ni herramientas. Se conserva el cierre original
incierto por separado: el lock libre observado después de salir no tenía un pin
previo y no demuestra el cierre del mismo lease. Su
[snapshot saneado](qa/product-background-native-20261104/SNAPSHOT.json) conserva
las fuentes y ambos recibos, sin perfiles ni credenciales.

E corrigió únicamente la observación acotada de procesos y llegó al caso real.
CUA observó A con avisos habilitados/silenciados (1/1), B vacío y desactivado
(0/0), B todavía igual tras terminar A y A de nuevo en 1/1. El único run de
Codex terminó con resultado conocido y limpieza confirmada, pero devolvió
«Prueba de fondo terminada.» sin ninguna llamada de herramienta. No creó la
pregunta canónica: el probe de espera falló sus condiciones y no se intentó
responder ni repetir el run. La causa de la omisión sigue sin establecerse.

E salió por GUI a los 330,088 s: doce identidades conocidas ausentes, el mismo
lease libre y desmontaje normal al primer intento, sin señales ni fallos de
recursos. El pico de la cuota fue 561.090.560 bytes dentro de 2 GiB; el proveedor
alcanzó 106.471.424 bytes de su límite muestreado de 128 MiB. Se conservaron las
imágenes y los originales. El
[snapshot de E](qa/product-background-native-20261104-e/SNAPSHOT.json) separa
estas comprobaciones aprobadas del caso funcional incompleto. La observación
de B mientras A espera sigue pendiente; un cierre normal no la sustituye.

## Alcance de esta entrega

El caso F añadió una observación real de B vacío y desactivado mientras A seguía
«En curso», emparejada con un recibo firmado del mismo run `running`. Sigue
parcial: el proveedor superó su sublímite de 128 MiB antes del estado final; el
supervisor detuvo la app y se confirmó después la ausencia de sus cuatro
identidades, el mismo lease libre y el desmontaje normal. No se amplió el límite
ni se repitió la consulta. La [evidencia F](qa/product-background-native-20261104-f/SNAPSHOT.json)
conserva esa parada, sin presentarla como cierre GUI normal.

Los tamaños, sin leer contenidos, identificaron 170.102.784 bytes en el proveedor:
un catálogo remoto de plugins de 58.605.568 bytes y un repositorio temporal entre
sus mayores archivos. La configuración `apps=false` no desactiva la sincronización
de plugins del cliente oficial 0.163.0. Se añaden `features.plugins=false` y
`features.remote_plugin=false`, ambos necesarios para impedir también la
alternativa Git. Los peers de autenticación/protocolo y herramientas dinámicas
rechazan el arranque si falta cualquiera de esos bloqueos. Esta corrección debe
aprobar la CI exacta y un caso nativo nuevo antes de declararse verificada. No
explica por sí sola que E terminara sin invocar la pregunta.

- Proyectos, repositorios y worktrees propios: selección explícita, estado/diff,
  historial y efectos Git revisados, con conservación de trabajo y recibos.
- Integración GitHub mediante cuenta elegida, repositorio/destino exactos,
  lectura y publicación revisadas; ninguna credencial ambiental del dueño.
- Preguntas estructuradas, progreso, presencia, sugerencias, paneles declarativos
  y notificaciones locales optativas con navegación al contexto correcto.
- Workspaces simultáneos por conversación, agrupación y fijado; colaboración
  entre identidades locales admitidas y comunicación correlacionada entre agentes.
- Presupuestos de tokens/coste con reserva y conciliación. Un consumo o precio
  no informado permanece desconocido; no se presenta como cero.
- Clientes locales firmados para operar estas capacidades desde CLI/TUI y ACP,
  conservando las mismas barreras y aprobaciones del servicio.

Son ampliaciones de A-14/17/18/19/20/50/61/63/64/68/79 y de sus conexiones. No
habilitan por silencio un proveedor de inferencia nuevo, servicios cloud,
colaboración por Internet ni clientes iPhone. La fase B sigue aplazada.

## Contratos de integración

La autoridad de efectos continúa siendo la de Runtime. Una revisión de proyecto
o un mensaje entre agentes son datos; nunca crean una autorización de herramienta.
Cada ejecución fija su sesión, agente, cliente, carpeta física y generación. Los
callbacks revalidan ese ámbito antes del efecto y la revocación cancela el run.
La selección global de carpeta deja de ser el destino implícito de otras sesiones.

Los temporales conservan registros y respuestas sólo en RAM. Las respuestas a
preguntas usan un canal efímero al turno que las solicitó; el host no añade sus
valores a recibos, SQLite o logs. Si el modelo cita una respuesta en su texto,
ese texto sigue la política normal de conservación del chat. Las credenciales
se introducen en controles
seguros del host y no son argumentos de herramientas del modelo.

Git se ejecuta mediante argv, sin shell ni hooks/filtros del repositorio, con
programa explícito, identidad física, límite de salida/plazo y cierre confirmado.
La lectura de un repositorio requiere seleccionarlo. No se recorren repositorios,
historiales ni credenciales personales para descubrir proyectos.

## Criterios de cierre

La CI completa debe aprobar Ubuntu/macOS, Swift, paquetes, bundle autocontenido y
smoke. Los recorridos nativos posteriores usarán perfiles/repositorios nuevos de
prueba bajo cuota y plazo: dos carpetas simultáneas sin contaminación; invitación
y revocación entre clientes firmados; pregunta/respuesta efímera y cancelación;
mensaje correlacionado sin transferencia de permisos; límites/uso desconocido;
proyecto/worktree con estado/diff y conservación; panel funcional y navegación.

El acceso real de GitHub y el permiso de avisos los concedió el dueño. Las
aprobaciones de los perfiles sintéticos se realizaron bajo su delegación. Las
pruebas locales no incluyeron suites completas ni compilaciones; los recorridos
de interfaz e IPC tuvieron cuotas, plazos y recibos propios. La CI exacta de
`1b0a7ead`, ejecución `41123446861`, pasó 3955 casos Python en Ubuntu (47
omitidos, 302,13 s), 4002 en macOS (ninguno omitido, 300,29 s) y ejecutó 710 Swift
(uno omitido, cero fallos, 170,872 s). También pasaron wheel/sdist, instalación
aislada, bundle/helper y smoke de cierre del servicio por EOF.

Siete selecciones de clientes verificaron CLI/TUI, el subconjunto ACP declarado,
colaboración, cancelación, A2A, presupuestos y revocación. Son 57 comprobaciones
finitas, no cobertura exhaustiva de esos protocolos. El uso observado incluyó
20 150 tokens; el coste no informado se conservó desconocido, con la reserva
separada. Diez planes Git sobre un repositorio sintético comprobaron worktrees,
snapshots y restauración con oráculos de archivos y conservación del original.
Las dos preguntas simultáneas tuvieron seis rechazos de contexto/forma/revisión,
consumo único y cierre confirmado. El oráculo de almacenamiento se limita a los
archivos y páginas propios seleccionados; no afirma nada sobre la conservación
interna del proveedor.

En la copia estable `4686f25`, dos solicitudes reales a Codex completaron tres
llamadas conocidas: un `publish_panel` y dos `suggest_next_actions`. El panel
oscuro quedó legible sobre la app clara; la lista de tareas, el progreso, la
fuente interna y el guardado de alto contraste se comprobaron en la interfaz.
Elegir una sugerencia sólo rellenó el borrador; su grupo siguió activo, como
exige el contrato de lectura. Abrir otro chat no trasladó el borrador ni el
panel. Descartar las sugerencias del segundo chat las dejó canceladas en la
revisión 2. Cada conversación conservó un run y dos mensajes, sin ejecución
adicional de las continuaciones.

El cierre de esta copia fue normal, antes de su plazo, sin señales: cinco
identidades conocidas ausentes, el mismo lease libre y ambos volúmenes
desmontados sin fuerza en su primer intento. El pico observado fue de
1 097 232 384 bytes dentro de la cuota física conjunta de 2 GiB. Los originales
y las imágenes se conservaron. Los cierres inciertos de intentos anteriores
siguen registrados y no se convierten retroactivamente en cierres normales.

## Clientes locales

La comprobación de avisos distingue el estado del producto de la respuesta de
UserNotifications. Una copia temporal de QA devolvió un error del sistema; no
se atribuye todavía a su ubicación ni a una denegación del dueño. El helper de
cualificación permite ahora únicamente una app autocontenida bajo
`<home-del-UID>/Library/Application Support/JarvisQualification/<32-hex>/Jarvis.app`,
con argumentos vacíos, identidad `local.jarvis.qa`, manifiesto/configuración
fijados y los mismos controles de propietario, enlaces y cambios. El home se
obtiene de `getpwuid`, no de `HOME`. Solicitud, recibo, perfil, proveedor y entorno
siguen limitados a `/private/tmp`. Esta excepción es del helper optativo de QA;
no activa perfiles ni amplía los destinos del producto. En el intento estable
anterior `4686f25`, macOS concedió el permiso y aceptó `add`; `delivered` revisión
3 y `resumed` revisión 4 no trazaban por sí solos un clic OS. El dueño confirmó
después el uso de «Ver chat» dentro de Jarvis, que consume y retira el aviso.
No se abrió su centro de notificaciones personal ni se cambiaron ajustes para
forzar aquel resultado. La ubicación estable y la identidad QA cambiaron juntas
y no prueban la causa del error temporal anterior. El caso nuevo de `1b0a7ead`
sí acredita el clic físico y la navegación descritos arriba, sin reescribir los
límites del intento previo. El identificador de firma del bundle original de
CI coincide con su Info.plist, comprobado sin ejecutar ese binario.

El paquete instala `jarvis` y `jarvis-acp`; también están disponibles mediante
`python -m jarvis_core.product_client` y `python -m jarvis_core.product_acp`.
Requieren `--state` y `--identity-file` explícitos. `identity-create --directory`
crea una clave en una carpeta nueva privada y muestra sólo su clave pública.
El dueño admite esa identidad desde Clientes locales en la app y después
comparte las conversaciones concretas; conocer un socket no concede acceso.

El TUI es una consola finita de comandos por líneas, con plazos y salida
acotada. ACP implementa su contrato v1 declarado de inicialización, sesión,
prompt/cancelación y actualizaciones sobre IPC firmado; no anuncia terminal,
filesystem ni MCP suministrados por el cliente. Las aprobaciones permanecen
en Jarvis. Una pérdida de respuesta conserva request_id y nunca repite el prompt.

## GitHub y conservación

El dueño seleccionó `GuillerJr/ecommerce`, introdujo la credencial directamente
en Jarvis y se comprobaron dos lecturas de rama/commit. La cuenta fue
`GuillerJr`, el repositorio 1038178262 y el HEAD observado
`ca6aca0382eee07c9e4b444e0af4cf4e72082719`. Se mostraron los datos como un
reporte externo de sólo lectura, sin verificación independiente. La conexión se
desconectó desde la interfaz. No se reutilizaron credenciales ambientales ni se
publicó una PR, comentario o cambio remoto. Ese recorrido de publicación sigue
sin ejercitarse; las pruebas de contratos no se presentan como una publicación
real. Los tokens permanecen en RAM con vencimiento; identidad, repositorio y
conexión se fijan en los argumentos revisados antes de cada publicación.

Los respaldos nuevos conservarán metadatos durables y blobs de snapshots en un
sidecar histórico validado. Al restaurarlos no se admiten rutas, clientes,
membresías, colas, reservas ni efectos de forma automática; el registro histórico
no se presenta como una configuración activa restaurada.
