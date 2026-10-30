# Producto, sesiones y coordinación avanzadas

Continuación solicitada el 30-10-2026. La implementación está conectada y la
[CI completa de `4686f25`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/41108646785)
aprobó Ubuntu, macOS, Swift, paquetes y el bundle autocontenido. Los recorridos
finitos de clientes, Git, GitHub, preguntas, carpetas y paneles tienen evidencia
separada en el [registro de cualificación](PHASE_A_PRODUCT_COORDINATION_QUALIFICATION.json).
Queda sin acreditar la presentación y el clic real de un aviso de macOS: el dueño
no encontró el aviso. El ACK del sistema y un estado posterior `resumed` no
demuestran por sí solos ese recorrido. Este bloque no se presenta como completo
ni cierra las familias amplias de la matriz; la fase B sigue aplazada.

La continuación cambia la etiqueta de `delivered` a «Aceptado por macOS» y
explica su límite en la propia fila. También fija la identidad al construir los
controles de preferencias, en lugar de tomarla al pulsarlos. Se añaden casos de
ACK y navegación interna, conservación del aviso A al consultar B sin permiso,
y render SwiftUI/AX A → B → A con una respuesta anterior demorada. Deben pasar
su CI remota; no se ejecutaron suites ni compilaciones locales. Un valor AX de
preferencias que no coincidió con el snapshot RPC de B se conserva como
observación sin causa establecida, no como una fuga de permisos demostrada.

## Alcance de esta entrega

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
de interfaz e IPC tuvieron cuotas, plazos y recibos propios. La CI exacta pasa
3952 casos Python en Ubuntu (47 omitidos), 3999 en macOS (ninguno omitido) y
ejecuta 705 Swift (uno omitido, cero fallos). También pasan wheel/sdist,
instalación aislada, bundle/helper y cierre del servicio por EOF.

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
no activa perfiles ni amplía los destinos del producto. El recorrido estable
pasó su CI y conserva recibos nuevos. macOS concedió el permiso y aceptó el
`add`; la interfaz/RPC registró `delivered` en revisión 3. Después apareció
`resumed` en revisión 4, pero no se trazó qué vía lo produjo y el dueño indicó
«No encuentro el aviso». Por ello no se acredita presentación física ni clic
OS. No se abrió el centro de notificaciones personal ni se modificaron sus
ajustes para forzar el resultado. La causa de la ausencia sigue sin establecerse;
la ubicación estable y la identidad QA cambiaron juntas y no prueban por sí
solas la causa del error temporal anterior. El identificador de firma del bundle
original de CI coincide con su Info.plist, comprobado sin ejecutar ese binario.

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
