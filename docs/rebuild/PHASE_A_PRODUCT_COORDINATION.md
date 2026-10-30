# Producto, sesiones y coordinación avanzadas

Continuación solicitada el 30-10-2026. Estado: implementación en curso; este
documento no acredita por sí solo integración, pruebas ni paridad completa.
La distribución autocontenida y los recorridos anteriores se conservan.

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

El acceso real de GitHub y cualquier permiso de notificaciones requieren la
intervención del dueño cuando la app esté preparada. Las pruebas locales no
incluyen suites completas ni compilaciones. Hasta tener recibos y CI exacta,
los nuevos recorridos permanecen pendientes de cualificación.

## Clientes locales

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

El dueño seleccionó `GuillerJr/ecommerce` para el recorrido real. Falta introducir
la credencial en Jarvis y cualificar ese recorrido. No se reutilizan credenciales
del entorno ni se publica una PR por el mero hecho de conectar la cuenta.
Tokens en RAM con vencimiento; identidad/repositorio/conexión se fijan en los
argumentos que revisa la autoridad antes de cada publicación.

Los respaldos nuevos conservarán metadatos durables y blobs de snapshots en un
sidecar histórico validado. Al restaurarlos no se admiten rutas, clientes,
membresías, colas, reservas ni efectos de forma automática; el registro histórico
no se presenta como una configuración activa restaurada.
