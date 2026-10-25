# Referencia de comportamiento para reconstruir Jarvis

Fecha: 24-10-2026. Este documento describe observaciones estáticas de OpenClaw y
contratos propuestos para una implementación propia de Jarvis en este repositorio.
No contiene código, recursos ni un adaptador ejecutable de OpenClaw. El clon externo
se conserva únicamente como material de investigación; no es la nueva aplicación.

## Identidad y alcance de la evidencia

- Referencia local: `/Users/guillerjr/Documents/Proyectos/Jarvis-Base-OpenClaw`.
- Revisión comprobada: `1e3ff306abaff29f03e7668d53b01c07f10294ac`, rama `main`.
- Manifiesto de esa copia: OpenClaw `2026.10.7`.
- Tipo de evidencia de esta revisión: lectura de fragmentos de fuente y documentación
  incluida en esa revisión. No se ejecutó OpenClaw, no se instalaron dependencias y
  no se consultaron cuentas, credenciales ni conversaciones del dueño.
- La extracción y los identificadores REA pertenecen al registro de cobertura que
  mantiene la investigación principal. **Este documento no atribuye evidencia REA
  a una observación realizada solamente mediante lectura de fuente.**

La cobertura es parcial. Se examinaron los puntos de entrada y fragmentos citados
abajo para responder preguntas concretas de ciclo de vida, autorización, MCP,
memoria y control nativo. No se revisó cada archivo, cada dependencia, cada ruta de
red ni cada proveedor. Leer un punto de control no demuestra que todas las rutas
del programa pasen por él. La existencia de pruebas en el proyecto de referencia
tampoco acredita un resultado observado en este Mac.

La primera parte de REA registrada por la investigación principal analizó el
manifiesto y cuatro archivos seleccionados: `run-orchestrator.ts`, el wrapper y la
política de `before-tool-call`, y `agent-bundle-mcp-manager.ts`. Su evidencia padre es
`ev_187d54eade88bc3011234d989dd9c4bf0744a2a3cc50e1feb61566acf0d11272`:
cuatro fuentes analizadas, cero fallos de análisis sintáctico, 1.990 incógnitas
semánticas y cobertura parcial. Esto acredita ese alcance estático; no permite
extender conclusiones a dependencias, rutas dinámicas, archivos no seleccionados
ni comportamiento ejecutado. Las demás observaciones de este documento se apoyan
en lectura de fuente, salvo que otro registro indique evidencia adicional.

Las rutas de la tabla son relativas al clon y los enlaces fijan la revisión; las
líneas se refieren exclusivamente a esa identidad.

## Observaciones que informan el diseño

| Pregunta | Observación estática y ubicación | Consecuencia propuesta para Jarvis |
| --- | --- | --- |
| ¿Quién es dueño de un turno? | La entrada del ejecutor captura generación y contexto de propietario antes de preparar la sesión. [run-orchestrator.ts:107](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/agents/embedded-agent-runner/run-orchestrator.ts#L107). | Una ejecución debe tener identidad y propietario explícitos antes de adquirir recursos o emitir efectos. |
| ¿Responder equivale a terminar? | El resultado al llamante y la limpieza del propietario tienen promesas diferentes; se drena el trabajo y se liberan recursos después. [run-orchestrator.ts:602](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/agents/embedded-agent-runner/run-orchestrator.ts#L602). | Separar respuesta visible, cancelación solicitada y asentamiento completo. La siguiente ejecución no puede reutilizar un recurso que sigue cerrándose. |
| ¿Qué aprueba la persona? | El hook conserva una copia separada de los parámetros cuando se solicita aprobación; los siguientes hooks pueden vetar pero no cambiar esa copia. [hooks.ts:912](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/plugins/hooks.ts#L912). La política rechaza cambios tras aprobación confiable y bloquea ante fallo del hook. [agent-tools.before-tool-call.policy.ts:363](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/agents/agent-tools.before-tool-call.policy.ts#L363). | La autorización cubre una operación concreta e inmutable. Una modificación posterior exige volver a validar y, si cambia el alcance, una nueva aprobación del dueño. |
| ¿Cuándo se validan los argumentos? | El wrapper comprueba cancelación, finaliza parámetros y valida la forma que efectivamente se ejecutará antes del efecto. [agent-tools.before-tool-call.wrapper.ts:419](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/agents/agent-tools.before-tool-call.wrapper.ts#L419). | Validar en la frontera de ejecución; la validez de la propuesta inicial no autoriza parámetros transformados. |
| ¿Cómo se comparte un servidor MCP? | El gestor entrega arrendamientos y, al adquirir, espera retiradas, trabajo previo y publicación de configuración dentro de exclusión por claves de runtime. [agent-bundle-mcp-manager.ts:52](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/agents/agent-bundle-mcp-manager.ts#L52). | Cada conexión necesita dueño, alcance, contador de uso y cierre comprobable. El catálogo disponible no concede permiso para ejecutar. |
| ¿La memoria puede estar degradada? | El gestor mantiene identidad del índice, trabajo en segundo plano, colas de sincronización y estado del proveedor. [manager.ts:55](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/extensions/memory-core/src/memory/manager.ts#L55). Su estado puede declarar búsqueda solamente por palabras cuando fallan embeddings. [manager.ts:576](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/extensions/memory-core/src/memory/manager.ts#L576). | Separar almacenamiento duradero, recuperación e indexación, y declarar capacidad real. Un cambio de embeddings requiere identidad de índice y reconstrucción compatibles. |
| ¿Cerrar memoria es inmediato? | El cierre es compartido mediante una promesa y espera trabajo pendiente, inicialización y sincronización; puede conservar errores y reintentar liberaciones. [manager.ts:666](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/extensions/memory-core/src/memory/manager.ts#L666). | Un reinicio o cambio de configuración debe esperar el asentamiento del almacén. Nunca declarar una escritura durable antes de su confirmación. |
| ¿Capturar equivale a controlar? | La misma frontera nativa distingue observación de pantalla y acción de entrada, valida permisos y generación y revisa cancelación antes de despachar. [MacNodeRuntime.swift:150](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/apps/macos/Sources/OpenClaw/NodeMode/MacNodeRuntime.swift#L150), [MacNodeRuntime.swift:220](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/apps/macos/Sources/OpenClaw/NodeMode/MacNodeRuntime.swift#L220). | Publicar capacidades separadas para observar y efectuar cambios. Una observación correcta no acredita autorización ni capacidad de entrada. |
| ¿Qué ocurre si una acción ignora la cancelación? | El servicio nativo contempla efectos tardíos y libera entrada retenida antes de admitir trabajo de otra generación. [ComputerActionService.swift:225](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/apps/macos/Sources/OpenClaw/ComputerActionService.swift#L225). | La cancelación debe invalidar autoridad sin esperar a la cooperación del modelo y conservar responsabilidad por teclas/botones hasta liberar o informar fallo. |
| ¿Aislar Codex aísla todo el usuario? | La documentación distingue estado por agente y hogar compartido; explica expresamente que el hogar de proceso se hereda y puede descubrir configuración y herramientas del usuario. [auth.md:76](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/docs/plugins/codex-harness-reference/auth.md#L76), [auth.md:109](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/docs/plugins/codex-harness-reference/auth.md#L109). | La variante propia debe tener estado y entorno explícitos. No importar ni explorar cuentas, herramientas o historial personal automáticamente. |
| ¿Quién cierra un proveedor? | El ciclo de vida conserva una función de liberación cuando arranca el servicio para no depender de importar módulos durante el cierre. [provider-runtime-lifecycle.ts:1](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/agents/provider-runtime-lifecycle.ts#L1). | El dueño que crea un recurso conserva su mecanismo de cierre. La aplicación debe poder parar aunque haya fallado una carga o actualización. |

Estas consecuencias son decisiones de diseño propias. No se afirma que sus nombres,
interfaces o implementación deban reproducir los de OpenClaw.

## Contratos propuestos para la variante propia

Los siguientes nombres describen conceptos, no una API ya implementada. Deben
concretarse en los contratos versionados de Jarvis y probarse antes de conectarlos
a la aplicación productiva. Se conserva el espacio técnico `jarvis`.

### Sesión, solicitud y ejecución

Una sesión identifica la conversación y su política de persistencia. Una solicitud
es una entrada del dueño. Una ejecución es un intento admitido para satisfacerla.
Una solicitud puede tener un reintento, pero un reintento no adquiere autoridad
para repetir un efecto cuyo resultado sea desconocido.

Cada ejecución tiene identificador único, sesión, origen, generación, política,
plazo y estado. Cada evento público identifica ejecución y secuencia. La interfaz
puede reconstruir su estado a partir de un snapshot y eventos posteriores sin
inventar decisiones o repetir efectos.

Estados conceptuales mínimos:

1. En cola: todavía no posee autoridad de ejecución.
2. Activa: prepara contexto o razona dentro de las capacidades admitidas.
3. Esperando al dueño: la operación sigue sin autorización de efecto.
4. Deteniéndose: su autoridad se ha invalidado y conserva trabajo de liberación.
5. Asentada: ya no quedan efectos ni recursos pendientes de ese propietario.

El resultado terminal distingue completada, fallida y cancelada. Una respuesta
visible puede existir antes del asentamiento; la interfaz debe poder mostrar esa
diferencia. Un cierre parcial no autoriza a anunciar que todo se detuvo.

La admisión y los cambios de generación son responsabilidad del núcleo. Los campos
de contexto confiable, conversación temporal y modo plan no se aceptan como hechos
porque el cliente los envíe. Una ejecución temporal no consulta ni escribe memoria
personal salvo un contrato futuro aprobado expresamente.

### Propuesta, autorización, efecto y resultado de una herramienta

La propuesta incluye herramienta registrada, argumentos estructurados, objetivo y
recursos afectados. Los datos de una página, pantalla, servidor MCP o resultado de
otra herramienta son información no confiable, no autorización.

Cada propuesta admitida recibe una identidad de operación nueva. El mismo par de
herramienta y argumentos dentro de la misma ejecución representa dos operaciones
distintas si se propone dos veces. Una aprobación de la primera no autoriza la
segunda; tampoco se impiden dos llamadas legítimas con sus respectivas decisiones.

La frontera obligatoria aplica, en orden, la existencia y esquema de la herramienta,
política del modo de trabajo, identidad del recurso, alcance solicitado, estado de
la ejecución, decisión del dueño cuando corresponde y precondiciones frescas. El
ejecutor recibe una autorización de uso acotado ligada al resumen inmutable de la
operación. Caducidad, cancelación, revocación, cambio de argumentos o recurso la
invalidan antes del efecto.

«En esta conversación» y «Siempre» representan ámbitos propios de Jarvis, con
revocación comprobable. No se sustituyen por un permiso upstream con un nombre
parecido. El dueño concede permisos TCC, escribe contraseñas y aprueba acciones.
La interfaz transporta su decisión firmada; el modelo y los plugins no la fabrican.

El resultado distingue cuatro hechos: ejecución admitida, efecto iniciado, efecto
conocido y objetivo verificado. Los errores deben ubicar la frontera alcanzada.
Una respuesta HTTP correcta o la salida de una herramienta no bastan para declarar
que el objetivo se cumplió. Si hay efecto incierto, se informa y se ofrece una
reconciliación de lectura; no se repite automáticamente una escritura.

La verificación usa observaciones independientes y condiciones explícitas. Para
añadir una línea a un documento, encontrar el literal no demuestra conservar el
contenido anterior, el salto de línea ni la posición final: esas condiciones
forman parte del contrato que debe comprobar el módulo de documentos/computadora.

### Cancelación y asentamiento

Solicitar la cancelación devuelve un recibo de recepción de la orden. Esperar al
asentamiento devuelve la constatación de que terminó la liberación. Son operaciones
diferentes. Los métodos IPC deben mantener sus plazos por debajo del cliente nativo
de 30 segundos; una liberación larga se sigue mediante estado y eventos.

Al cancelar se invalida inmediatamente la generación que autoriza nuevos efectos.
Se cancelan las esperas de aprobación y se detienen nuevas llamadas de herramientas.
Cada propietario sigue siendo responsable de sus conexiones, tareas, transacciones
y entrada sintética hasta liberarlos. Una segunda cancelación no puede abandonar
esa limpieza y permitir que una ejecución nueva reutilice recursos semicerrados.

Cancelar un trabajo todavía en cola no cierra los recursos de otra ejecución. Un
fallo al cerrar un recurso tiene estado y diagnóstico propios; no se oculta bajo
una respuesta final genérica.

### Proveedores y motor de conversación

El motor de conversación posee el bucle de propuestas y resultados, y la frontera
de herramientas conserva la única autoridad final de ejecución. La implementación
propia no debe apilar varios planificadores que puedan actuar independientemente.
Un transporte de inferencia o harness externo solo puede usar las capacidades que
el contrato admite y no debe tener una ruta nativa de ejecución que evite esa
frontera.

La primera conexión sigue siendo Codex con ChatGPT mediante el flujo oficial,
con entorno y estado propios. La identidad de proveedor/modelo se conserva desde
el origen hasta evaluación y diagnóstico. Los modelos se eligen explícitamente,
sin respaldos entre proveedores. NVIDIA no recibe inferencia, memoria ni audio.
Las futuras conexiones locales y APIs tendrán adaptadores y capacidades declaradas.

Cambiar modelo, cuenta o transporte requiere una transición que respete ejecuciones
activas y su asentamiento. El proveedor declara texto, imágenes, propuestas de
herramientas y audio por separado. Que una cuenta permita conversar no acredita
que ofrezca un servicio de voz.

### Memoria y migración de datos

El almacenamiento cifrado, los espacios de datos, la recuperación y la indexación
son responsabilidades distintas. Toda escritura confirmada lleva identidad y
versión; la recuperación conserva procedencia y evita presentar una inferencia
como un recuerdo literal. Borrar u olvidar debe retirar también las proyecciones
derivadas correspondientes.

Un índice identifica proveedor, modelo, dimensiones y versión de extracción. Una
configuración incompatible produce reconstrucción controlada, nunca mezcla de
vectores. El diagnóstico declara si hay recuperación semántica, solo búsqueda por
texto, sincronización pendiente o indisponibilidad. Las políticas de degradación
se definen explícitamente, preservando la prohibición de respaldos de inferencia.

La migración de datos personales necesita versión, verificación, recuperación y
reversibilidad. La investigación utiliza datos sintéticos y no abre los almacenes
ni el historial del dueño para deducir requisitos. Un cierre de memoria espera sus
escrituras y trabajos antes de liberar la base de datos.

### MCP y extensiones

Cada servidor tiene identidad, transporte, dueño y capacidades. La sesión adquiere
un arrendamiento de conexión y lo libera al asentarse. Retirar un servidor o cambiar
credenciales no destruye recursos usados todavía por otra ejecución sin una
transición explícita. La configuración y el catálogo tienen versión.

Una herramienta MCP pasa por la misma frontera de autorización y verificación que
una herramienta propia. Un catálogo recordado puede servir para diagnóstico, pero
no para afirmar que un servidor está conectado ni para ejecutar con un esquema
obsoleto. OAuth y disponibilidad son estados separados de consentimiento de efecto.
Los plugins no deben disponer de un ejecutor alternativo que eluda esa política.

### Interfaz, dispositivo y audio

SwiftUI presenta mensajes, estados y decisiones del dueño. El daemon organiza
sesiones y trabajos. Los auxiliares firmados del dispositivo realizan observación,
entrada y audio dentro de sus capacidades y permisos. Ninguna capa interpreta una
pantalla o una frase de un modelo como aprobación de la persona.

La observación de pantalla tiene identidad, ventana/app objetivo, tiempo y alcance.
La acción de entrada comprueba que esa observación sigue siendo válida y que el
recurso coincide con lo aprobado. El control del dispositivo posee un ámbito de
entrada y libera teclas/botones al cancelar, desconectar o invalidar su generación.
No se debe anunciar entrada disponible porque la captura de pantalla funcione.

Audio se divide en activación/escucha, captura, reconocimiento, conversación,
síntesis y reproducción. Cada tramo declara disponibilidad, permiso, proveedor y
propietario. La interrupción detiene la reproducción y la autoridad de la ejecución
correspondiente. La escucha y el almacenamiento de audio no se activan por heredar
una configuración externa o por haber conectado el chat.

## Pruebas que deben resolver las incógnitas

| Recorrido | Evidencia mínima antes de declarar paridad |
| --- | --- |
| Sesión y cancelación | Interrumpir una respuesta, enviar otra inmediatamente y comprobar ambos estados terminales y el asentamiento sin cerrar el transporte nuevo. |
| Herramientas | Propuesta válida, veto, aprobación, ejecución y comprobación independiente. Un efecto incierto no se repite. |
| Permisos por app | El dueño elige cada alcance, se comprueba reutilización entre chats cuando corresponde, se revoca y se constata que una nueva acción queda pendiente sin modificar el recurso. |
| Documento | Escritura literal y adición de línea con conservación íntegra, salto y posición comprobados en un documento sintético. |
| MCP | Conectar un servidor sintético, ejecutar lectura, retirar/cancelar durante una llamada y comprobar liberación sin ejecutar con catálogo obsoleto. |
| Memoria | Guardar datos sintéticos, reiniciar, recuperar con procedencia, olvidar y verificar ausencia en almacenamiento e índices derivados; fallos de indexación con estado veraz. |
| Proveedor | Login hecho por el dueño, catálogo real, modelo elegido conservado, respuesta y propuestas; desconexión sin recuperar otra cuenta ni otro proveedor. |
| Audio | Reconocer español, responder por voz e interrumpir; verificar permisos, captura/reproducción y ausencia de envíos a un proveedor no elegido. |
| Seguridad entre capas | Probar las rutas de plugins, nodos, harness y conexiones directas; ninguna puede ejecutar sin la autoridad obligatoria del nuevo núcleo. |

## Primer contrato ejecutable propio

`src/jarvis_core/runtime.py` contiene un prototipo genérico independiente,
sin conexión con el daemon, la app ni las herramientas específicas del Jarvis
actual. Sus dependencias son de la biblioteca estándar de Python. No carga código
de OpenClaw ni un runtime externo. Las pruebas inyectan proveedores, herramientas
y decisiones ficticias, sin cuentas, archivos del dueño, dispositivos ni red.

Esta primera pieza verifica serialización por conversación, separación entre
cancelación y asentamiento, aprobación ligada a una operación y argumentos
inmutables, presupuesto de pasos y conservación de efectos inciertos. No ejecuta
ningún mecanismo de aprobación real; el protocolo de grant es una frontera
sintética que necesitará una implementación autenticada antes de producción.

La respuesta final se etiqueta como texto del modelo (`answer_source="model"`),
mientras los recibos de acciones son registros separados. El prototipo no prueba
la verdad semántica de una frase ni convierte una afirmación del modelo en un
efecto verificado. Si se interrumpe o falla una herramienta después del despacho,
conserva un recibo incierto y detiene ese recorrido sin repetir el efecto.

Los callbacks inyectados siguen siendo código de confianza: una acción nativa que
ignore cancelación necesita las barreras por efecto y generación del módulo de
dispositivo; este prototipo en memoria no puede detener por sí solo un programa
externo. Persistencia, autenticación, IPC, interfaces, memoria y herramientas
concretas no se acreditan mediante este núcleo aislado. El servicio añadido tiene
pruebas propias de SQLite e IPC y un cliente oficial Codex separado; su estado se
documenta en [BASELINE_VERTICAL_SLICE.md](BASELINE_VERTICAL_SLICE.md). El análisis
completo por partes y la cualificación de todas las familias siguen pendientes.

## Bloques OC-03 y OC-04

El tercer bloque añade el gateway, salud, inyección de chat y observación de
sesiones; el cuarto, ciclo MCP, propiedad de preguntas, cliente y servidor stdio.
Los manifiestos y resúmenes `REA_PART03_*` y `REA_PART04_*` conservan hashes, blob
Git, Evidence IDs y límites. Se analizaron cuatro archivos TypeScript por bloque,
sin ejecutar OpenClaw ni resolver todas sus dependencias dinámicas.

Contratos que orientan la reconstrucción: comprobar salud actual sin exponer
información sensible; separar chat temporal de almacenamiento durable; esperar
el cierre del propietario de una conexión y no asignar una pregunta externa a un
usuario ambiguo. La implementación propia se verifica aparte. La lectura estática
no acredita equivalencia de comportamiento en ejecución.


## Bloques OC-10 y OC-11: Telegram por capas

Se registraron ocho fuentes TypeScript seleccionadas de transporte API, entradas
runtime, claves de sesión, autorización/pipeline de entrada y persistencia de
offset. Cada lote mantiene un archivo inventariado fuera de selección; su
cobertura de aplicación es parcial. La semántica sigue unknown, con 124 y 837
incógnitas respectivamente. Los resúmenes y procedencia están en
[REA_PART10_SUMMARY.json](REA_PART10_SUMMARY.json),
[REA_PART10_PROVENANCE.json](REA_PART10_PROVENANCE.json),
[REA_PART11_SUMMARY.json](REA_PART11_SUMMARY.json) y
[REA_PART11_PROVENANCE.json](REA_PART11_PROVENANCE.json).

La separación útil para el contrato propio es transporte, identidad/ruta,
autorización y cursor durable. Esa separación no prueba cada helper importado ni
un intercambio real del canal. Jarvis requiere decisiones explícitas del dueño,
una ruta de chat privado y recibo durable; el texto del canal no aprueba herramientas.
Las diferencias y el alcance probado están en
[PHASE_A_CHANNELS_VOICE_WEB.md](PHASE_A_CHANNELS_VOICE_WEB.md).

## Bloque OC-12: selección web, extracción y SSRF

Cuatro fuentes TypeScript y el manifiesto forman el artefacto acotado. REA marca
aplicación complete sólo dentro de ese artefacto; la semántica permanece unknown
con 791 incógnitas. Las vistas finas conservan Evidence IDs para selección de
proveedor, resolver/política DNS, lookup fijado, extractor y páginas de hechos en
[REA_PART12_SUMMARY.json](REA_PART12_SUMMARY.json). La adquisición documentó la
cadena Git tree y los hashes por archivo en
[REA_PART12_PROVENANCE.json](REA_PART12_PROVENANCE.json).

La selección de proveedor, extracción legible y validación DNS son fronteras
distintas. Su presencia estática no acredita los plugins importados, el recorrido
HTTP/redirecciones completo, TLS/proxies reales o que pasen las pruebas de la
referencia. Las preguntas de pinning, mezcla público/privado y cancelación guían
pruebas de código propio; no autorizan autodetección de credenciales, excepciones
de red o respaldos de proveedor en Jarvis.

No se ejecutó OpenClaw en estos lotes. El cierre target-free de OC-12 y un fallo
de readiness de Ghidra ajeno al análisis JavaScript se conservan como límites,
sin afirmar snapshot de una app que no se abrió ni reparar herramientas ajenas.
Con OC-12, el registro alcanzó doce lotes y 48 rutas TS/JS únicas seleccionadas.
Ese antecedente no cerró los scopes restantes ni acreditó revisión completa.

## Bloque OC-13: respaldo, restauración y propiedad de mantenimiento

La partición selecciona cuatro módulos de comandos SQLite, verificación de
archivos, restauración a staging y exclusión de mantenimiento doctor. Los árboles
Git Root/Src/Commands y sus blobs se recalcularon hasta la revisión fija; la copia
inerte procede de GitHub y no de leer o modificar el clon. Ver
[procedencia](REA_PART13_PROVENANCE.json) y [resumen REA](REA_PART13_SUMMARY.json).
REA conserva aplicación **partial**, aunque no quedaron archivos inventariados
fuera de selección, y semántica **unknown** con 1429 incógnitas. No se ejecutó
OpenClaw ni sus pruebas.

El wrapper de SQLite exige elegir global o agente y delega crear/verificar/
restaurar al proveedor; su consistencia real con WAL queda fuera de estas cuatro
fuentes. La vista de `backupSqliteCreateCommand` conserva
`ev_71cc85ded490c3742e0d0856549f742a04cbe1070e3bb575a03549d6d6daa47c`.
El [verificador](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/commands/backup-verify.ts#L484)
comprueba manifiesto único, duplicados/colisiones, enlaces y snapshots con ámbito
e integridad antes de preparar extracción. Los parsers y el proveedor importados
no quedan acreditados por observar esas llamadas. Vista de preparación:
`ev_d2716e8af8d154975b3c35f46f693962b25cc2ef78a9ca68e833987ebcb02db3`.

La [restauración](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/commands/backup-restore.ts#L277)
prepara un destino nuevo o vacío fuera del estado activo y deja la activación
como paso separado. Las advertencias de rollback incluyen aprobaciones y dedup;
no prueban que sea seguro reutilizar permisos o credenciales. Un fallo de limpieza
conserva el error inicial y el secundario. La vista
`ev_15fbd81c9b16cf4bb0602f3aa8bc9907a20854fe02e5e6be1d14e848723fe26b`
es estática, sin restaurar ningún perfil.

El [wrapper doctor](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/commands/doctor-sqlite-maintenance-lock.ts#L158)
solicita propiedad exclusiva compartida con el dueño del estado, valida rutas y
aliases, exige autoridad vigente y la expira al terminar; revalida después de
esperar una reconciliación. Evidence ID
`ev_de3feb59164c14651be93ab41ae447ef8961f6c8ba7f5a96b08fa7665760edd4`.
El algoritmo interno del lock y los demás diagnósticos/reparaciones doctor siguen
fuera de la partición.

Estos contratos orientan una implementación propia futura: corte coherente,
verificación antes de staging, activación explícita y reporte de sólo lectura
separado de reparaciones autorizadas. **No añaden implementación de respaldo,
restauración o doctor a Jarvis ni cierran una capacidad de A.** El export conserva
ocho preguntas de alcance, además de las incógnitas semánticas; proveedores de
snapshots, parser/manifiesto, credenciales, motores remotos y ejecución permanecen
desconocidos. Con OC-13 se alcanzaron trece lotes y 52 rutas TS/JS únicas;
ese antecedente no acredita paridad ni revisión exhaustiva.

## Bloque OC-14: copia SQLite, publicación e integridad

Cuatro imports del bloque anterior conducen al proveedor local, su copia canónica
y los helpers de snapshot/integridad. La [procedencia](REA_PART14_PROVENANCE.json)
recalcula la cadena Git Root/Src/Snapshot/Infra y los blobs de la revisión fija.
El [resumen](REA_PART14_SUMMARY.json) conserva aplicación **partial**, semántica
**unknown** con 2342 incógnitas y seis vistas finas. No se ejecutó la referencia.

La [ruta de copia](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/infra/sqlite-snapshot.ts#L552)
liga validación y llamada a backup bajo lectura/transacción fijada, normaliza el
journal de la copia privada y por defecto la compacta después de transformarla.
Comprueba integridad/owner de la salida y publica con digest e identidad. La ruta
de bytes crudos está separada para un dueño detenido/checkpointed. El wrapper
canónico distingue global/agente y llama sanitizadores importados: no acredita
todavía qué filas o credenciales eliminan. Vistas
`ev_e0232263b12a2d2ffabaf408f332e2ebbe9df3c2158cef4da96a968cf62427cd` y
`ev_53295fbd996022ddc225a40c8bdcbb539b9137133dbeb1ec17c4f421da1edbbd`.

La [publicación](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/infra/sqlite-snapshot.ts#L358)
revalida bytes/identidad y durabilidad y conserva un recibo unknown si un intento
de reemplazo queda incierto. Un snapshot usa .pending hasta verificar DB/manifiesto
y sincronizar. La [lista del proveedor](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/snapshot/local-repository.ts#L491)
puede recuperar un complete-pending y retirar ese marcador: **no es sólo lectura**.
El contrato propio futuro debe separar listar/diagnosticar de recuperación
explícita, conservando esa capacidad de A. restoreFresh exige destino nuevo,
sin sidecars y fuera del repositorio. Vistas de publicación/resultado incierto:
`ev_fab81a294dc098d596c2bbed7004519d7977691d7ea44368e8e8ffaece3f8f1c` y
`ev_85fbec7e3faf26844a5017037f75695ad799922962822afd5dbdc9314437e48a`.

La [integridad](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/infra/sqlite-integrity.ts#L208)
incluye PRAGMA directo, claves foráneas y clasificación FTS sólo con definiciones
propias exactas. No sustituye presupuestos de trabajo ni validación de motores.
Evidence ID `ev_2a7ca97e65603401a4195dc6a554c3c0669285f3951159b74f3b0935dfbe10a6`.

La coherencia de un snapshot individual no vuelve atómico un perfil multi-store.
Siguen fuera de la partición los helpers de familia DB/WAL/SHM, workers/backup
nativo, sanitización/archivos fríos, manifiesto/FD/fsync, motores remotos y ejecución
real. **No se implementan ni se cualifican backup/restore/doctor en Jarvis por esta
lectura.** Catorce lotes y 56 rutas TS/JS únicas amplían una investigación parcial;
no cierran la fase A ni la equivalencia de ninguna familia.

## Bloque OC-15: activación inmutable, recuperación y firma

Se incorporan cuatro fuentes TypeScript nuevas del mismo pin para control de
instalación, publicación del puntero activo, runtime independiente de recuperación
y primitivas Ed25519. La [procedencia](REA_PART15_PROVENANCE.json) verifica blobs
y la cadena Git Root/Src/Infra. El clone permaneció dataless: sólo se consultaron
metadatos y se obtuvieron bytes del GitHub oficial por SHA. Son copias de fuente
inertes, sin ejecución de OpenClaw ni observación de una distribución instalada.

El [resumen](REA_PART15_SUMMARY.json) conserva aplicación **partial**, semántica
**unknown** y 1290 incógnitas. Padre:
`ev_f45ebe76e095370d3793afaaceb626edb46ed03f4181cc1977b725f13f32422d`.
La selección pequeña incluye los cuatro módulos y el manifiesto; cero archivos
relevantes fuera de selección no acredita sus imports ni el repositorio completo.

El [control inmutable](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/infra/package-update-activation-immutable.ts#L491)
comprueba identidad física, descriptor, revisión y registro esperado; distingue
SHA de generación, buildDigest y formato de control. Una operación de activación
pendiente bloquea nueva preparación. Preparar una generación no concede publicar
su puntero ni autoridad sobre el servicio. La custodia real y los helpers de
transacción quedan fuera de las cuatro fuentes. Vista de módulo:
`ev_fa297ed77934cbab4f2881839dd6bf238f4a5f9d9436f40aeae4c86fed5ee10f`.

La [publicación del puntero](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/infra/package-update-activation-immutable-pointer.ts#L63)
conserva intención durable e identidades anterior/temporal/destino antes de
reemplazar current. La reconciliación distingue el estado anterior, una
publicación coincidente o un objeto discrepante que conserva la recuperación.
Las llamadas a rename y sync no acreditan atomicidad conjunta de journal, app,
servicios y datos ni comportamiento ante corte eléctrico. Vista:
`ev_7386e7b7441bdc3f96d8e4ca98107f332a1a8e310b53ea627e6a1db82baf2fae`.

La [recuperación](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/infra/package-update-activation-immutable-recovery.ts#L129)
prepara y compara una copia independiente con la instalación, runtime y generación
registrados; no prueba que un arranque fallido termine en rollback satisfactorio
ni que una versión anterior pueda leer datos migrados. Vista:
`ev_7767e68693826a53a1ea5a2fdb72efceaa01abf2cb18f5c9a9f617c2b27dd805`.
La [primitiva Ed25519](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/infra/ed25519-signature.ts#L166)
verifica payload con una clave aportada por el llamador. No queda acreditada su
conexión con catálogo, manifiesto, asset descargado, firmante confiable, revocación
o firma/notarización macOS. Vista:
`ev_51b39a525c5a8b5ade92edc30d99000416b5247e7d99e5552b68dccbf3f788ee`.

Los contratos propios derivados separan descargar/preparar/activar/recuperar,
requieren revisión y autoridad ligadas a identidades vigentes y mantienen
incertidumbre sin replay automático. No reutilizan permisos de backup ni tools y
no confunden rollback de código con restauración de datos. Son requisitos de
diseño futuro, no evidencia de nuevas capacidades implementadas en Jarvis.
El export completo permanece en la ruta temporal registrada, fuera del repositorio.
Con OC-15 se alcanzan quince lotes y **60 rutas TS/JS únicas**. La investigación
sigue parcial; este lote no cierra distribución, actualización, rollback ni otra
familia de la fase A.

## OC-16 — cierre del gateway y custodia del proceso hijo

Se analizaron cuatro fuentes nuevas del mismo pin, como texto inerte, con
`package.json` como contexto. Las identidades y observaciones seleccionadas están
en [provenance](REA_PART16_PROVENANCE.json) y [summary](REA_PART16_SUMMARY.json).
No se ejecutó OpenClaw ni se modificó su clon. El lote registra 3967 nodos AST,
110 hallazgos, cero errores de parseo y **793 incógnitas semánticas**. Agotar las
dos páginas de observaciones estáticas no elimina esas incógnitas ni prueba comportamiento vivo.

El [presupuesto de cierre](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/gateway-shutdown-budget.mjs)
separa tiempo de drenaje, reserva de limpieza y margen del supervisor. La
[custodia CLI](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/cli-parent-lifetime.mjs#L41)
requiere una relación de lanzamiento explícita; consume sus marcadores antes de
que los hereden trabajadores desligados y atiende la desconexión del canal.
La terminación forzada prevista por esa fuente no demuestra limpieza satisfactoria.
Jarvis conserva sus propios límites y pruebas de identidad, sin copiar constantes
ni código de la referencia.

La [secuencia de cierre](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/gateway/server-shutdown.ts#L30)
distingue uniones obligatorias de errores que pueden agregarse: un fallo requerido
detiene el avance hacia dependencias posteriores. El mismo archivo conserva por
separado error de arranque y error de limpieza. Eso es una estructura observada;
no acredita por sí sola todos los propietarios de las funciones importadas.

La [preparación](https://github.com/openclaw/openclaw/blob/1e3ff306abaff29f03e7668d53b01c07f10294ac/src/gateway/server-close.ts#L216)
invoca una barrera de escrituras de sesión antes de su primera espera y registra trabajo de
limpieza bajo un propietario propio. El retiro posterior sigue esperando trabajo
seguido aunque haya consumido su gracia inicial, y distingue advertencias de
fallos de recursos. La coordinación completa con registros, memoria y otros
gateways depende de helpers fuera del lote; no se da por verificada.

Contrato propio derivado para el cambio de perfil: terminar un proceso o agotar
una gracia no prueba cierre correcto. La app debe conservar el propietario ante
incertidumbre y no iniciar otro perfil hasta acreditar el cierre previsto. El
nuevo perfil necesita objetos de sesión, clientes y contexto independientes;
una respuesta tardía no puede aplicarse sólo porque coincida un ID de chat.
Son criterios de implementación y pruebas, no una declaración de handoff ya listo.

Con OC-16 el registro alcanza **16 lotes y 64 rutas TS/JS únicas**, incluidas dos
MJS. La cobertura global y semántica sigue parcial. El export completo de cinco
registros permanece fuera del repositorio; no se distribuyen los cuerpos de la
referencia dentro de Jarvis.
