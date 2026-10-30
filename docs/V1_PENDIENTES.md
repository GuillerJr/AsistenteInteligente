# Pendientes de Jarvis

Decisión del dueño, 24-10-2026: **primero construir la variante completa funcional
de OpenClaw con el nombre Jarvis, mediante investigación con REA y código propio**.
Las mejoras propias se harán después. Se limpió este repositorio; el código del
producto anterior sigue en Git y sus inventarios están congelados.

Alcance inicial elegido: macOS Apple Silicon y Telegram como primer canal externo.
El dueño aplazó expresamente la app de iPhone; ver [decisiones](rebuild/PHASE_A_TARGETS.md).

El [bloque de producto y sesiones avanzadas](rebuild/PHASE_A_PRODUCT_COORDINATION.md)
ya implementa proyectos/GitHub, preguntas/progreso/presencia/sugerencias, paneles,
avisos optativos, carpetas por conversación, colaboración/A2A local, presupuestos
y clientes CLI/TUI/ACP. La [evidencia por checkpoint](rebuild/PHASE_A_PRODUCT_COORDINATION_QUALIFICATION.json)
conserva la CI completa de `4686f25`, siete selecciones de clientes, dos lecturas
GitHub, diez planes Git y dos preguntas simultáneas con seis rechazos y consumo.
La copia estable de ese commit acreditó panel oscuro legible, sugerencias sólo
como borrador, descarte y aislamiento entre dos chats. Cerró normalmente, sin
señales ni procesos conocidos retenidos, y desmontó ambas cuotas sin fuerza.
El dueño concedió avisos y macOS aceptó una solicitud; el aviso no fue localizado
por el dueño, por lo que su visualización y clic real siguen sin acreditarse.
Se añade una corrección de texto para distinguir aceptación de visualización,
con regresiones que deben pasar su propia CI remota antes del cierre.
La matriz actual tiene **62 familias parciales, 18 sin implementar y ninguna
completa**. REA registra **48 lotes y 186 rutas TS/JS únicas** seleccionadas.
Las cifras de cierres anteriores que siguen abajo pertenecen a sus checkpoints.

Alcance elegido cualificado: [distribución autocontenida y servicio residente](rebuild/PHASE_A_DISTRIBUTION_RESIDENT.md).
La app incorpora Python, dependencias, Codex y MiniLM; el registro de inicio en
macOS es optativo. La [CI exacta de `29b730c`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/41069453178)
acreditó el paquete, su traslado y cierre por EOF; el pin del manifiesto está en
Info.plist firmado. El [registro](rebuild/PHASE_A_DISTRIBUTION_RESIDENT_QUALIFICATION.json)
conserva también el recorrido nativo aprobado: respuesta real, residencia, reapertura y salida sin procesos retenidos. No se registró un
Login Item real ni se probó macOS 14. Los servidores MCP externos pueden requerir
programas/runtimes propios. La firma pública, notarización y actualización pública
siguen pendientes; la membresía y credenciales del dueño no se sustituyen por la
firma ad hoc de CI.

El incremento de [MCP, plugins y skills](rebuild/PHASE_A_MCP_EXTENSIONS.md) está
cualificado en el alcance elegido; sus [oráculos y CI](rebuild/PHASE_A_MCP_EXTENSIONS_QUALIFICATION.json)
se conservan junto al [cierre narrativo](rebuild/PHASE_A_MCP_EXTENSIONS_CLOSEOUT.md).
La matriz actual incluye también reproducción de medios: 54 familias parciales y 26 sin
implementación, ninguna familia amplia completa. REA conserva 45 lotes y 171
rutas TS/JS seleccionadas; no es cobertura exhaustiva.

El incremento elegido de **documentos y medios locales** está cualificado al
checkpoint [450621f](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40951214228):
corpus finito con fuentes/citas, negativos de PDF cifrado/tamaño excesivo, retirada
y reproducción nativa con pausa, búsqueda, cambio entre las salidas observadas y
desconexión sin fallback. Se conservan el fallo OCR tipográfico, el positivo
legible y el alcance muestreado del vídeo; no es cobertura universal de formatos.
Ver [evidencia y límites](rebuild/PHASE_A_TELEGRAM_VOICE_MEDIA_QUALIFICATION.json).

El incremento elegido de **Telegram, voz, documentos y multimedia** está
[cualificado en su alcance finito](rebuild/PHASE_A_TELEGRAM_VOICE_MEDIA.md). Telegram acredita
privado/grupo, rechazo, retirada y pausa/reactivación sin replay. El recorrido
privado controlado A/B quedó aprobado: notas nativas, reingreso con secuencias 3/4
y huellas coincidentes, última nota inspeccionada una vez con aprobación, «73»,
cita temporal y respuesta correlacionada; ocho comprobaciones del oráculo final.
Se conservan los fallos de fidelidad de las grabaciones ambientales del grupo,
que el resultado privado no cualifica. Wake en 450621f falló en el resultado de
origen/prefijo sin causa establecida. Talk acreditó tres turnos conocidos, las respuestas «4» y
«6» audibles, cero herramientas y entrada/salida inactivas tras detenerse. La
[CI completa de `9d4edfb`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40982348625)
aprobó 3526 casos Python en Ubuntu (46 omitidos), 3572 en macOS y 634 Swift
(uno omitido, cero fallos), bundle/helper, paquetes y smoke. Wake controlado
conservó el borrador y añadió sólo «el marcador es 42.» después de retirar el
prefijo, sin crear mensajes. El diagnóstico se ocultó en otro chat y se conservó
al regresar. El caso de dos menciones confirmado por el dueño no establece la
causa de la captura histórica 450.
La copia 9d cerró con Salir antes del plazo: supervisor cero, sin señales, siete
identidades ausentes y mismo lease libre, con pico observado de 384,56 MiB dentro
del nuevo presupuesto de 448 MiB. Una instancia adicional observada después se
cerró y reconcilió por separado; no se atribuye un mecanismo interno de apertura
sin traza. Los recibos y límites están en el registro integrado. Se conservan el
cierre por plazo de 450, su intento anterior incierto y los fallos anteriores.
Este cierre elegido no completa ninguna familia amplia ni la fase A; la matriz
sigue con 54 parciales y 26 sin implementar, y la fase B continúa aplazada.

## Fase A — base completa primero

El [incremento de automatización](rebuild/PHASE_A_AUTOMATION.md) está cualificado
en el alcance elegido: cron, heartbeat, wake, hooks, webhook local y flujos con
checkpoints. Nueve recorridos nativos, diagnóstico y cierre tienen oráculos
separados; el producto del bundle d474 es idéntico al de 1481983, cuya
[CI exacta](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40877800263) pasó completa.
Ver [registro](rebuild/PHASE_A_AUTOMATION_QUALIFICATION.json) y
[cierre](rebuild/PHASE_A_AUTOMATION_CLOSEOUT.md). No quedan pendientes dentro de
ese incremento. La matriz actual conserva 54 familias parciales y 26 sin implementar,
ninguna familia amplia completa; REA mantiene 45 lotes y 171 rutas TS/JS.

- [x] Limpiar el árbol de trabajo y conservar puntos de recuperación.
- [x] Conservar inventarios de capacidades del Jarvis anterior para la fase posterior.
- [x] Fijar la referencia de OpenClaw y empezar su análisis REA por bloques.
- [ ] Completar la investigación y los recorridos de la
  [matriz de paridad](rebuild/OPENCLAW_BASELINE_PARITY.md); el censo no es revisión.
- [ ] Cualificar el producto: el recorrido de desarrollo ya acredita app SwiftUI,
  servicio, IPC, sesiones SQLite, inferencia autenticada con Codex oficial,
  cancelación, reinicio y temporales. Falta cualificación de distribución completa.
- [ ] Implementar y verificar las herramientas de la propia base OpenClaw, sesiones
  avanzadas, agentes, memoria base, MCP, skills, extensiones, programador, canales,
  nodos y superficies funcionales incluidas en la matriz. No moverlas a la fase B
  por coincidir con capacidades del Jarvis anterior.
  Avance conectado: autoridad de herramientas, archivos, memoria léxica local y
  gestión MCP stdio desde la app; ver [alcance y límites](rebuild/PHASE_A_TOOLS_MEMORY_MCP.md).
  La [continuación de gestión](rebuild/PHASE_A_MANAGEMENT.md) añade procesos aislados,
  perfiles de agente, biblioteca local de skills, programador y ramas de conversación.
- [ ] Completar aislamiento de ejecución y autorizaciones reales del dueño, sin
  confundir identidad de clave admitida con atestación Apple, ni un ACK con
  cierre de una acción. La identidad local firmada ya está implementada en el
  bloque de núcleo de abajo; el aislamiento completo sigue pendiente.
- [ ] Distribución nativa autocontenida, actualización, recuperación e instalación
  cualificadas; no reemplazar la app anterior con un bundle de desarrollo.
- [ ] Acreditar la base completa mediante pruebas por capacidad y recorridos reales.
  La fase A sigue abierta hasta cumplir los criterios de la matriz.

## Continuación de canales, voz y web — cualificación pendiente

El [nuevo registro](rebuild/PHASE_A_CHANNELS_VOICE_WEB.md) documenta Telegram
privado como primer canal externo, vinculación con dos revisiones del dueño,
voz local y lectura HTTPS autorizada por URL. El checkpoint `381074d` tiene
1234 pruebas Python y 129 nativas, paquetes y CI aprobados en Ubuntu y macOS.
Los recorridos reales registrados usan únicamente el perfil sintético: vinculación,
suma, escritura desde Telegram aprobada y verificada por bytes/hash, lectura
HTTPS externa y una frase de dictado añadida al borrador sin enviarse. La lectura
de una frase sintética volvió a reposo sin error visible; no hubo medición acústica
independiente. Una detención posterior de Telegram tuvo causa no establecida.
Estos recorridos acotados no cierran todas las capacidades del canal, voz o web.

- [ ] Cualificar Telegram con chat privado elegido por el dueño: entrada/respuesta
  únicas, agente/sesión/destino exactos, recibo durable y denegación/revocación.
- [x] Observar ambas revisiones de vinculación, creación de ruta y una suma real
  de prueba con salida confirmada, sin herramientas; alcance limitado registrado.
- [x] Observar tarjeta y resultado de escritura desde Telegram en el chat elegido
  sin recarga del agente, con aprobación del dueño y archivo/hash coincidentes.
- [ ] Completar comprobaciones reales de expiración/cancelación y del refresh nativo,
  sin ruta implícita, mensaje automático ni comandos `/start` enviados al modelo.
- [ ] Cualificar dictado y lectura locales en el Mac objetivo, con TCC del dueño,
  borrador conservado, sin autoSend ni audio a nube, NVIDIA o proveedor no elegido.
- [x] Comprobar una frase real es-ES mediante el modelo local moderno ya instalado,
  conservando «BORRADOR BASE» y sin enviar ningún mensaje.
- [ ] Cualificar `web_fetch` por la app: aprobación/revocación, límites y cita externa,
  sin buscador, JavaScript, cuentas ni descarga de archivos.
- [x] Comprobar una lectura real de `https://example.com/` con aprobación y recibo
  HTTP 200; contenido externo no verificado, sin JavaScript ni enlaces adicionales.
- [ ] Registrar controles finales de cierre Codex/MCP/Telegram/scheduler/RPC,
  reteniendo Stores y propietarios ante incertidumbre y sin reconexión/replay.
- [ ] Completar la investigación semántica pendiente: REA 13–20 amplían respaldo,
  snapshots, actualización, cierre, migración de memoria, custodia SQLite y archivos;
  los lotes OC-21A/B/C añadieron núcleo y sesiones: su checkpoint tenía 23 lotes
  y 90 rutas TS/JS. El bloque actual de Mac/navegador amplía el registro a 27
  lotes y 105 rutas; ninguna de esas cifras cierra la matriz ni toda la referencia.
- [x] Ejecutar Python/Swift/paquetes/bundle y CI para el checkpoint `b03ad7a`:
  2321 pruebas Python y 282 Swift. El [cierre nativo](rebuild/PHASE_A_RECOVERY_NATIVE_CLOSEOUT.md)
  conserva identidades y límites; los controles de incrementos posteriores se registran aparte.

macOS Apple Silicon sigue como destino inicial e iPhone permanece aplazado por
petición expresa. Estas implementaciones parciales no equivalen a todos los
canales, navegador completo o voz continua. La fase A continúa abierta.

## Continuación de recuperación — fase A

El [registro de recuperación](rebuild/PHASE_A_RECOVERY.md) conserva el alcance y los
límites de respaldo portátil, staging restaurado y diagnóstico. Ninguna familia se
declara completa por los tests sintéticos.

- [x] Probar el núcleo propio con SQLite real, autoridad central y barrera del host:
  publicación sin sobrescritura, cancelación, recibo incierto, restauración nueva
  desarmada y diagnóstico sin inicializar fuentes ausentes.
- [x] Cualificar los recorridos nativos finitos de respaldo, verificación y
  restauración solicitados en los puntos 1 y 2, con decisiones reales/delegadas
  separadas de las pruebas IPC sintéticas. [Cierre](rebuild/PHASE_A_RECOVERY_NATIVE_CLOSEOUT.md).
- [x] Observar una copia, su verificación y la preparación de otro perfil desde
  la app con aprobaciones del dueño, usando únicamente datos sintéticos. Recibos
  de publicación y perfil desarmado observados; la ampliación finita posterior
  está registrada en el cierre nativo y no constituye trabajo nuevo.
- [x] Implementar captura y diagnóstico de SQLite frías DELETE independientes,
  sin inicializar stores ni modificar las fuentes.
- [x] Añadir captura WAL compatible en copias privadas para respaldo y diagnóstico:
  estado, memoria y tareas; último commit, límites y propietario retenido ante
  limpieza incierta. [Alcance y pruebas](rebuild/PHASE_A_WAL_RECOVERY.md).
- [x] Comprobar diagnóstico, respaldo y verificación WAL en la app con aprobación
  real del dueño y un perfil sintético: memoria WAL, estado/tareas DELETE; último
  commit, hashes y cinco originales intactos verificados independientemente.
  [Evidencia y límites](rebuild/PHASE_A_WAL_RECOVERY_LIVE.json). La ampliación posterior de variantes está en el [cierre nativo](rebuild/PHASE_A_RECOVERY_NATIVE_CLOSEOUT.md).
- [x] Restaurar ese respaldo WAL desde la app con aprobación del dueño a otro
  perfil sintético desarmado: último commit, IDs/ámbitos y datos conservados;
  cinco originales y seis archivos del respaldo intactos. [Evidencia](rebuild/PHASE_A_WAL_RESTORE_LIVE.json).
  Aclarar origen/hash y cifras del perfil activo en la revisión; ampliar el caso
  IPC para comprobar los tres SQLite restaurados. El destino permanece sin activar.
- [x] Comprobar diagnóstico y revisión WAL de sólo lectura en la app de prueba,
  con originales intactos; corregir la colisión que ocultaba el aviso de último
  commit y comprobarlo visualmente. [Evidencia y límites](rebuild/PHASE_A_WAL_RECOVERY_LIVE.json).
- [x] Observar diagnóstico y revisión nativos con memoria y tarea sintéticas no
  vacías, conservando los tres SQLite. El informe global fue parcial por recursos
  ausentes; no se ejecutó otro roundtrip nativo en ese recorrido de lectura.
- [x] Impedir arranque accidental del perfil restaurado antes de crear locks,
  stores o conexiones, sin impedir el cierre de un host que ya estaba abierto.
- [x] Implementar activación local con autoridad central y apertura explícita con
  servicio nuevo, controles de identidad y elección independiente del proveedor;
  [pruebas y límites](rebuild/PHASE_A_ACTIVATION.md). No reasocia memoria ni reactiva canales.
- [x] Observar activación y apertura del perfil sintético con ambas decisiones
  del dueño, servicio nuevo, cuenta desconectada y consulta local de memoria,
  diagnóstico y revisión de respaldo; límites en el registro de activación.
- [x] Observar otro arranque del mismo perfil sintético admitido, con restricciones
  exactas, sin cuenta ni procesos hijos del proveedor y con controles intactos.
Los negativos de activación y la reconciliación de memoria/canales se siguen
en la continuación actual de abajo, sin duplicar este hito histórico.
- [x] Integrar el núcleo de copia atómica de ámbitos de memoria y compatibilidad
  de esquema v1/v2 con respaldo, diagnóstico y restauración de historia.
  [Contrato y pruebas](rebuild/PHASE_A_MEMORY_RECONCILIATION.md).
- [x] Conectar esa copia a la autoridad y corte del host y a la interfaz nativa;
  probar lectura sin efectos, copia/denegación, carreras, límites y cierre mediante
  fixtures propios, incluida la custodia tras perder una respuesta.
- [x] Comprobar la copia autorizada desde la app de dos notas sintéticas (175 bytes):
  originales intactos, IDs nuevos, revisión 1, recibo único y procedencia verificados
  independientemente; consulta nativa coincidente sin replay. [Evidencia y límites](rebuild/PHASE_A_MEMORY_COPY_LIVE.json).
- [x] Reproducir y corregir el descarte de una pulsación durante el sondeo de
  estado, con pruebas del Store, gesto en vista real con transporte falso y cola
  IPC serial real; [evidencia y límites](rebuild/PHASE_A_OWNER_CLICK_REGRESSION.json).
- [x] Investigar la demora inicial y corregir la exposición del arranque a
  configuración de venv, hooks de usuario y variables Python heredadas. Hay
  reproducciones causales y arranque canónico aislado verificado. La atribución
  al archivo o causa de sistema históricos precisos no está acreditada; no se
  inventa a partir del fixture. [Investigación](rebuild/PHASE_A_STARTUP_ISOLATION.md).
- [x] Implementar las variantes frías WAL ausente, vacío y sólo cabecera válida,
  y journals DELETE/PERSIST calientes o PERSIST/TRUNCATE inactivos, mediante
  recuperación privada y rechazo de familias ambiguas. Los originales se conservan.
- [x] Implementar archivos/artefactos de workspace con selección exacta, archivo
  portátil independiente, inventario y destino nuevo, sin selección ni ejecución automática.
- [x] Implementar reparación explícita del índice FTS desde datos base válidos y
  del prefijo WAL hasta el último commit verificado, publicando otra copia. La
  reparación de prefijo declara pérdida posible y transacciones perdidas desconocidas.
- [x] Implementar actualización y rollback locales de desarrollo: pareja app/Python
  propia, CURRENT transaccional, copia actual verificada y compatibilidad de esquemas;
  recibos y propietario retenido ante incertidumbre, sin downgrade de datos ni replay.
  El launcher comprueba ambos componentes de la misma versión y los esquemas fríos
  antes del arranque explícito; no activa perfiles restaurados.
- [x] Comprobar desde la app copia/restauración de archivos, respaldo con journal
  caliente, reparación FTS/restauración e instalación v1 → v2 → rollback v1, con
  decisiones del dueño y verificación independiente sobre datos sintéticos.
  Arranque real de la pareja recuperada app/Python y cierre correctos, sin cuenta.
  2297 pruebas Python y 249 Swift aprobadas; paquetes y bundle comprobados.
  [Implementación y límites](rebuild/PHASE_A_RECOVERY_COMPLETION.md), informados por
  [OC-19](rebuild/REA_PART19_SUMMARY.json) y [OC-20](rebuild/REA_PART20_SUMMARY.json).
- [x] Completar la matriz nativa finita: siete modos WAL/journal con tres roles,
  nueve prefijos dañados, 16 revisiones GUI y tres rechazos GUI. Los efectos de
  todos los casos se comprueban por IPC nativo con decisiones sintéticas; los
  recorridos autorizados/delegados publican y restauran prefijos de memoria y
  tareas con proyecciones SQL y originales verificados. Se comprueban denegación,
  caducidad, cancelación e interrupción sin replay. [Matriz](rebuild/PHASE_A_RECOVERY_NATIVE_MATRIX.md).
- [x] Reproducir y corregir decisiones ocultas por pestaña, caducidad, prioridad
  de un resultado anterior, apertura de Ajustes y pérdida de conexión. Aviso en
  la ventana principal, acceso a la operación exacta y retirada del ticket tras
  desconexión comprobados en la app. No se renuevan permisos ni se repiten
  solicitudes automáticamente. [Cierre de los puntos 1 y 2](rebuild/PHASE_A_RECOVERY_NATIVE_CLOSEOUT.md).

### Activación, copia de memoria y canales — continuación actual

Los tres contratos locales quedaron implementados y verificados. Sus
observaciones GUI, recibos e identidades se conservan en el [registro de reconciliación](rebuild/PHASE_A_RECONCILIATION_CLOSEOUT.md).
Las casillas siguientes separan implementación de comprobación nativa final.

- [x] Admitir inventario, revisión y copia explícita sobre familias frías WAL y
  journals íntegros compatibles. La revisión conserva los archivos; la apertura
  y recuperación SQLite sólo ocurren después de la aprobación exacta. La copia
  conserva las notas de origen y su procedencia, sin prometer inmutabilidad física
  de la base después del efecto autorizado.
- [x] Conectar habilitación/deshabilitación e inspección local de rutas conservadas
  a la autoridad central, con recibos durables, incertidumbre y custodia de limpieza
  sin replay. La decisión local conserva conversación e historial y no inicia el canal.
- [x] Exigir prueba fresca del chat por nonce y claim antes de activar Telegram:
  prueba sólo en RAM, ligada a conexión/ruta/agente/workspace, sin restaurar tokens
  o permisos. El lector descarta mensajes previos al arranque y todo su primer
  segundo parcial; las fechas no convierten el historial en entrada nueva.
- [x] Añadir regresiones adversariales de activación y hacer que su validación
  nativa respete también el vencimiento de la revisión, sin ampliar el TTL.
- [x] Consolidar los negativos y recorridos GUI finales de activación, copia de
  memoria y reconciliación local en perfiles sintéticos, incluidos sus límites.
- [x] Aprobar los controles locales integrados: 2402 pruebas Python y 305 Swift,
  paquete e instalación aislada, además del bundle y los recorridos GUI. Los hooks
  repiten los controles sobre el árbol congelado antes de publicar; la CI de cada
  commit se comprueba por separado.
- [ ] Completar distribución cualificada. El canal `development` mantiene Python
  y Codex externos como opción de desarrollo. El runtime autocontenido y su
  traslado pasaron CI en `29b730c`, con el recorrido nativo registrado por separado
  y aprobado. Siguen pendientes autenticidad pública, Developer ID/notarización
  y actualización remota firmada; no se presenta el checkpoint histórico como
  prueba del nuevo paquete.

A-71 y la fase A siguen abiertos; la fase B no ha comenzado.

### Núcleo y sesiones — contrato finito completado

El [cierre de núcleo y sesiones](rebuild/PHASE_A_CORE_CLOSEOUT.md) conserva
fuentes, identidades, recorridos nativos y límites. Se completó este bloque
solicitado; las familias amplias de la matriz y la fase A siguen abiertas.

- [x] Admitir clientes locales con Ed25519, ámbitos de lectura/gestión,
  revisión exacta, revocación durable y revalidación antes del efecto. El bearer
  de descubrimiento no concede identidad ni se presenta como firma.
- [x] Recuperar el servicio propio tras caída comprobada, con perfil físico y
  lease exactos, custodia de hijos y carga exclusivamente local. Retirar las
  aprobaciones anteriores y conservar `interrupted/unknown`, sin replay ni
  reconexión del proveedor por la recuperación automática.
- [x] Compactar contexto con decisiones literales y recibos históricos,
  conservar originales y continuar desde otra rama sin deshacer efectos ni
  heredar permisos. Consultar operaciones durables tras pérdida de ACK/reinicio.
- [x] Coordinar objetivos de hasta cuatro hijos, con sesiones y aprobaciones
  separadas, presupuestos, plazos, cancelación y asentamiento durable.
- [x] Encolar hasta cuatro sucesores y corregir un turno exacto sin reintento
  implícito; bloquear la cola ante resultados anteriores inciertos.
- [x] Conectar estos controles a SwiftUI, probar carreras, pérdida de respuestas,
  revocación, temporales, reinicios y fuentes conservadas. Nueve decisiones
  nativas delegadas, escritura hija comprobada por bytes/hash, caída con
  Mantenimiento abierto y reapertura completa del perfil sintético verificadas.
- [x] Aprobar 2540 pruebas Python y 332 Swift, bundle de desarrollo y paquete
  aislado. Los hooks repiten los controles antes de publicar; la CI corresponde
  al commit resultante. El peer de modelo de estos recorridos es propio y
  sintético; no se repitió inferencia autenticada con la cuenta del dueño.
  La primera CI detectó dependencia accidental de metadatos de índice en el
  smoke offline; una reproducción con caché nueva y cuatro regresiones fijan
  la instalación directa desde el lock, sin red ni compilación de dependencias.

La investigación REA OC-21A/B/C es estática y conserva incógnitas. No acredita
la orquestación distribuida, todos los harnesses, agrupación de sesiones,
acceso remoto o atestación Apple. Esos ámbitos conservan sus estados en la
matriz; no se trasladan a B ni se vuelven a abrir los cierres anteriores.

### Herramientas del Mac y navegador — implementación y cualificación actual

El [cierre del incremento](rebuild/PHASE_A_MAC_BROWSER_CLOSEOUT.md) y su
[registro de evidencia](rebuild/PHASE_A_MAC_BROWSER_CLOSEOUT.json) distinguen
implementación, focales aprobados y controles pendientes. Esta lista no reabre
los bloques históricos ni declara completa una familia amplia de la fase A.

- [x] Implementar diff UTF-8 multiarchivo con altas, ediciones, bajas y movimientos,
  contexto/hashes exactos y preflight de todas las rutas. Publicación atómica por
  archivo; fallo parcial con recibo incierto, sin rollback sobre cambios ajenos.
- [x] Implementar PTY real con stdin/EOF, salida, resize, señales y cancelación,
  custodia de descriptores y Seatbelt sin fallback. Programas admitidos de un solo
  proceso; no shell arbitrario con fork o pipelines.
- [x] Conectar observación y acciones AX del Mac a destino/ventana/observación
  exactos y autoridad central. Frame opcional de ventana en RAM, redacción de
  controles protegidos y canal efímero de imágenes, sin base64 durable.
- [x] Implementar navegador WebKit propio no persistente y conexión CDP a una
  pestaña elegida explícitamente; refs DOM/origen exactos, navegación, historia,
  entrada y cierre. Proxy y reglas de contenido del navegador administrado;
  no se atribuye esa política de red al navegador externo adjunto.
- [x] Conectar búsqueda a plantilla elegida por el dueño y descarga explícita de
  hasta 64 KiB a una ruta nueva del workspace, con lease original y hash comprobado.
- [x] Integrar permisos de conversación/«Siempre», temporales, revisión CAS y
  revocación en broker/UI; invalidar permisos de búsqueda al cambiar su destino.
  La cancelación conserva el lane mientras falte una callback real: `stopLoading`
  no demuestra terminar JavaScript ya despachado ni actividad autónoma del sitio.
- [x] Aprobar los focales disponibles: 151 Python de patch/PTY/custodia, 52 de
  broker/permisos y una selección integrada de 108; 39 nativos de Mac/navegador/
  auditoría. Los conjuntos se solapan y no se suman como total de pruebas únicas.
  Después pasaron nueve de host/lifecycle, 31 de CAS AX y 91 Python del indicador
  de descarga, incluidos cinco + cinco + dos casos nuevos respectivamente.
- [x] Cualificar Chrome 155.0.8059.85 real con un perfil nuevo propio: un caso
  opt-in aprobado, sin enumerar perfiles ni pestañas personales. Verificar también
  55 operaciones de la app en ejecución por RPC firmado: WebKit, CDP real, patch,
  descarga, PTY y una apertura HTTPS pública, con oráculos independientes.
  SDK y decisiones sintéticos;
  dos denegaciones y una ref obsoleta son negativos esperados, no éxitos.
- [x] Registrar la ejecución completa Python actual: 2672 aprobadas en 488,93 s,
  sin cambios Python posteriores. Conservar el checkpoint previo de 2670.
- [x] Registrar Swift completo: 392 casos, una omisión Chrome opt-in y cero
  fallos en 154,796 s. Después cambió sólo la separación ACK/postcondición AX:
  tres archivos Swift y cinco tests nuevos, focal de 33 aprobado. No se afirma
  que los 392 anteriores cubrieran esa corrección ni se anticipa el próximo total.
- [x] Compilar y firmar el bundle de desarrollo 03; conservar el manifiesto
  histórico Python de 173 rutas, el checkpoint de 355 y el actual de 356 fuentes/
  scripts/tests, comparado con el árbol congelado. No se cambia la app personal.
- [x] Corregir autoaprobación mediante ventanas propias, custodia WebKit falsa,
  carreras de lifecycle, CAS por grupos AX vacíos e indicador de descarga publicada.
  El positivo AX del build 02 falló de forma controlada; no se cuenta como escritura.
- [x] Cualificar AX entre procesos: set_value Unicode/salto de línea y type_text
  con selección UTF-16, oráculo AppKit externo y longitud final de 41 unidades.
  Cualificar frame real de 14.300 bytes sólo en memoria, con controles protegidos
  retirados y sin portador en SQL/log ni archivos de captura de control.
- [x] Confirmar descarga nueva con archivo/hash y `written_to_workspace=true`.
  Conservar el press anterior incierto aunque el contador externo sea 1: sin replay.
- [x] Elegir «Siempre» en la tarjeta nativa por CUA y comprobar en otro chat el
  mismo permiso `approval.remembered`, sin tarjeta ni decisión RPC del observador.
- [x] Verificar el bundle 04: el fixture anterior había salido; el destino nuevo
  exigió aprobación y no heredó su regla. Una operación nueva 0→1 pasó con
  postcondición/ACK separados; frame real de 12.352 bytes sin portador durable.
- [x] Confirmar «Siempre» por UI para el destino nuevo y persistencia tras
  reiniciar los mismos bytes de la app. Resolver/ventana revalidaron los handles
  RAM explícitamente; luego la misma observación pasó sin tarjeta ni decisión RPC.
- [x] Revocar la regla nueva por UI y rechazar la aprobación renovada: SQL durable
  `approval_denied`, cero recibos y sin despacho. Retirar también la regla del
  destino antiguo; revisión 9, cero permisos, acciones pendientes o runs no terminales.
  Las decisiones sólo afectan a perfiles sintéticos nuevos.
- [x] Publicar el bloque en `66cafb8` y el presupuesto de CI en `38a27d1`, con
  hooks locales completos: Python, 397 Swift sin omisiones, Chrome real,
  paquetes/smoke y bundle. Ubuntu remoto pasó; macOS detectó dos fallos de
  preparación con `startupUnconfirmed`, sin invalidar los recorridos ya observados.
- [x] Reproducir la dependencia de DNS inverso del fixture y retirarla mediante
  un servidor HTTP numérico explícito. Dos regresiones distinguen DNS bloqueado
  y ausente; la selección focal de 26 casos pasó con Chrome real y el bootstrap.
  [Diagnóstico y límites](rebuild/PHASE_A_CI_STARTUP.md). Los hooks y la CI del
  commit que contiene esta corrección registran el resultado global posterior.
- [x] Eliminar la espera circular del executor en las dos pruebas Python de
  cancelación de activación/limpieza: notificación asíncrona, escenarios con pool
  normal y un solo hilo, sin relajar recibos, custodia o comprobaciones de replay.
  El archivo focal completo pasó 28 casos; el diagnóstico enlazado conserva
  las reproducciones anteriores a la corrección.
- [x] Confirmar la corrección `89021c6` en CI Ubuntu y macOS: 2674 pruebas Python
  y 399 Swift, con una omisión declarada de Chrome opt-in, paquetes y bundle.
  [Ejecución aprobada](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40546090306).
- [x] Retirar las suites completas automáticas de los hooks locales por elección
  del dueño. Selecciones explícitas con cuota APFS, plazo, diagnóstico acotado,
  identidad completa de fuentes y custodia incierta conservada; admisión pytest
  antes de fixtures. Los 65 focales pasaron con cierre físico comprobado.
  [Contrato y evidencia](rebuild/TEST_RESOURCE_LIMITS.md). La cualificación global
  del supervisor pasó en Ubuntu y macOS para `c137c57`:
  [CI aprobada](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40561444150).

La lectura pública única de `https://example.com/` pasó por el proxy privado,
sin excepción sintética, y cerró su binding; conserva contenido externo no
verificado. TCC efectivo se comprobó con la identidad dedicada nueva y la
excepción explícita del dueño; Touch ID lo completó el dueño. El intento denegado
previo se conserva. Los fixtures y el SDK propio no acreditan inferencia
autenticada; las credenciales y perfiles personales siguen fuera de la delegación.

REA OC-22A/B1/B2/C mantiene manifiestos, blobs, hashes e incógnitas. La cobertura
del checkpoint Mac/navegador era parcial: **27 lotes, 105 rutas TS/JS únicas**,
47 familias con avance parcial y 33 sin implementación. No son porcentajes de
producto terminado; memoria y MCP amplían posteriormente ese registro.
OC-23 añade cuatro particiones de memoria: **31 lotes y 118 rutas TS/JS únicas**
en el registro actual. Estudia búsqueda híbrida, sincronización, recuperación
activa y mantenimiento con REA, sin ejecutar la referencia ni copiar su código.
Conserva 2.907 incógnitas semánticas de estos nuevos grafos.
[Fuentes, Evidence IDs y contratos propuestos](rebuild/REA_OC23_MEMORY.md).
El dueño eligió expresamente MiniLM multilingüe local en CPU el 27-10-2026;
Codex con ChatGPT permanece como proveedor de conversación. Los embeddings
permanecen locales y no existe fallback externo.

### Memoria local — alcance elegido

- [x] Búsqueda léxica, semántica y combinada con citas de bytes/revisión vigentes,
  encoder CPU real fijado y caché derivada acotada, sin texto en el índice vectorial.
- [x] Indexación automática de cambios de notas aprobadas, por generaciones y
  sin releer URI, carpetas personales ni historial.
- [x] Recuperación fresca por turno con consentimiento local revocable;
  guardado, corrección y olvido verificados con ChatGPT en el perfil sintético.
- [x] Abstención ante coincidencias débiles: se conserva el fallo real inicial
  del distractor y su repetición corregida con cero citas.
- [x] Preparación/verificación del modelo, reconstrucción/verificación del índice,
  cancelación, reparación derivada, recibos y custodia ante cierre incierto.
- [x] Panel nativo estable y valores de accesibilidad actualizados;
  revocación comprobada mientras la nota permanecía guardada.
- [x] Cualificar el elemento AX estable: recorrido real 5→6→7, suite Swift
  completa y bundle aprobados en `10e4d65`; Python completo aprobado en ambos
  sistemas con el subárbol productivo idéntico de `075b9dd`. El cierre documental
  inicia otra CI completa, tras un fallo de descarga pública en Ubuntu.

[Contrato y límites](rebuild/PHASE_A_MEMORY_INTELLIGENCE.md) ·
[Registro de cualificación](rebuild/PHASE_A_MEMORY_QUALIFICATION.json).
Este bloque indexa notas autorizadas. No memoriza conversaciones automáticamente,
no borra retroactivamente información ya enviada a un proveedor y no incorpora
wiki, motores alternativos ni datos del Jarvis anterior sin elección explícita.

La distribución autocontenida posterior se acredita en su
[registro separado](rebuild/PHASE_A_DISTRIBUTION_RESIDENT_QUALIFICATION.json).
Developer ID/notarización y actualización remota firmada siguen pendientes.
La fase A sigue abierta y la fase B no ha comenzado.

### MCP, plugins y skills — incremento elegido cualificado

El [registro del incremento](rebuild/PHASE_A_MCP_EXTENSIONS.md) y la
[continuación del cierre](rebuild/PHASE_A_MCP_EXTENSIONS_CLOSEOUT.md) conservan
por separado implementación, fallos iniciales, regresiones y recorridos del
bundle exacto. El [registro saneado](rebuild/PHASE_A_MCP_EXTENSIONS_QUALIFICATION.json)
conserva el estado `qualified_selected_increment`; la fase A global sigue abierta.

- [x] Implementar transportes elegidos con protocolo explícito, OAuth PKCE,
  recursos/prompts/plantillas y validación local revisada de esquemas.
- [x] Conectar Apps WebKit y relay loopback a la autoridad común; retirar
  renderer/capacidad antes de cambiar fuentes y conservar cierres inciertos.
- [x] Implementar paquetes declarativos propios, instalación/actualización
  deshabilitadas, activación, reversión, retirada y configuración MCP separada.
- [x] Implementar skills con argumentos, recursos congelados, versiones y
  reversión; preparar contexto no envía un turno ni concede herramientas.
- [x] Añadir regresiones de cierre de Codex, agentes con plugins, lecturas
  tardías y lifecycle nativo de Apps; conservar los fallos de checkpoints previos.
- [x] Registrar la cualificación nativa final: OAuth/renovación/revocación,
  SSE, servidor público compatible, App/puente/retiro, relay independiente y
  ciclos completos de paquetes/skills con sus oráculos y recibos.
- [x] Aprobar Python, Swift, bundle, wheel/sdist y smoke del último árbol en
  CI remota, sin repetir suites ni compilaciones en el Mac.

El checkpoint histórico MCP conserva **38 lotes y 144 rutas TS/JS seleccionadas**;
el registro actual, ampliado por automatización, conserva 45 lotes y 171 rutas.
Los catálogos restantes
siguen abiertos. Los paquetes propios no son plugins OpenClaw compatibles ni
acreditan sus 177 conectores. El relay no es un servicio OAuth de Internet; las
capacidades de Apps no anunciadas, los servicios no elegidos y la distribución
autocontenida se mantienen separados. La fase A sigue abierta y B no ha comenzado.

## Automatización — incremento elegido cualificado

- [x] Implementar cron IANA, heartbeat con franja y wake durable sin replay.
- [x] Conectar hooks declarativos, HMAC loopback y flujos con checkpoints.
- [x] Verificar dos escrituras con aprobaciones separadas, pausa y reanudación.
- [x] Verificar dos cron y un heartbeat con Codex real; detener sin más ocurrencias.
- [x] Verificar firma/fecha/replay/conflicto HTTP con cliente independiente y cierre del puerto.
- [x] Revocar, suspender/reanudar y cancelar desde la app durante aprobación pendiente; archivos ausentes y resultados desconocidos conservados.
- [x] Reabrir con reglas/recibos persistentes, motores y webhook detenidos, sin nueva inferencia.
- [x] Confirmar cierre propio y picos de 58.634.240 y 58.650.624 bytes bajo 256 MiB; originales conservados.
- [x] Verificar el bundle d474 posterior a las correcciones con Codex real, Doctor nativo y salida limpia; pico final de 108.523.520 bytes bajo 256 MiB, sin señal del observador.
- [x] Aprobar Python, Swift, bundle, paquetes y smoke de la revisión exacta 1481983: CI 40877800263 completa en Ubuntu y macOS. Los fallos anteriores de DTO, compilación del fixture y expectativas de mantenimiento se conservan en el registro.

Este incremento local no instala un daemon OS, despierta físicamente el Mac o
publica un webhook de Internet. No acredita push, iPhone ni distribución; esos
bloques siguen separados. La fase A global continúa abierta.

## Producto y sesiones avanzadas — entrega en validación

El [contrato del incremento](rebuild/PHASE_A_PRODUCT_COORDINATION.md) conserva el
alcance solicitado y la revisión [REA OC-28](rebuild/REA_OC28_PRODUCT_COORDINATION.md).
Hay implementación propia y pruebas nuevas escritas; no se han ejecutado suites
ni compilaciones completas en el Mac.

- [x] Conectar proyectos/worktrees/GitHub, preguntas/paneles, organización,
  colaboración y workspaces por conversación al servicio y SwiftUI.
- [x] Incorporar reservas inclusivas, medición correlacionada y cancelación del
  árbol, sin inventar uso/costes ausentes ni prometer topes de facturación.
- [x] Incorporar clientes CLI/TUI/ACP firmados y conservación desarmada de los
  metadatos nuevos en respaldos.
- [ ] Aprobar la CI completa del commit exacto y corregir sus fallos.
- [ ] Cualificar los recorridos nativos con datos sintéticos y GitHub elegido
  `GuillerJr/ecommerce`, credencial introducida por el dueño.

## Fase B — después de acreditar A

- [ ] Mejoras propias y herramientas adicionales del
  [inventario backend](rebuild/JARVIS_BACKEND_INVENTORY.md) y
  [nativo](rebuild/JARVIS_NATIVE_INVENTORY.md).
- [ ] Migración reversible y ensayada con datos sintéticos del producto anterior.
- [ ] Repetir las pruebas históricas que correspondan, incluida aprobación,
  revocación y verificación real de resultados. No se declaran resueltas por rehacer.

La conexión elegida sigue siendo Codex con ChatGPT; NVIDIA queda excluido y no hay
respaldo automático entre proveedores. El dueño inicia sesión, escribe contraseñas,
concede TCC y aprueba acciones reales. Los pendientes anteriores permanecen en el
historial del commit `c97ed89`; no se han borrado de los inventarios.
Para este incremento el dueño delegó expresamente las aprobaciones en perfiles
sintéticos; no incluye contraseñas ni perfiles personales. La excepción TCC
puntual actual se registra arriba; no cambia la regla general de concesión por
el dueño ni delega credenciales.

Checkpoint histórico `381074d`: 1234 pruebas Python y 129 nativas aprobadas. Se verificaron
una escritura desde Telegram con aprobación y hash, una lectura HTTPS marcada como
externa y un dictado local de español añadido al borrador sin enviarse. Son recorridos
acotados en el perfil sintético, no cierre de las familias ni de la fase A.
