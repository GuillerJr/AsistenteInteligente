# Revisión de herramientas con REA — 22 de octubre de 2026

La [segunda revisión](CODEX_REA_FOLLOWUP.md) amplía la cobertura y documenta las
correcciones posteriores de web, proyectos, adjuntos, tareas y objetivos.

Se revisó el artefacto instalado de Codex y se contrastaron sus superficies con
las herramientas de Jarvis. Las mejoras son implementaciones propias sobre el
código de Jarvis. No se incorporó código, recursos, textos de interfaz ni paquetes
de la aplicación analizada. Tampoco se consultaron credenciales ni historial del
dueño.

## Artefacto y cobertura

- Aplicación: `/Applications/ChatGPT.app`, identificador `com.openai.codex`,
  versión `26.1016.51333`.
- ASAR: `Contents/Resources/app.asar`, 626.348.697 bytes.
- SHA-256: `9d0260dd37f9c45546d5a89d9d782b8362c9957b70b992e2c3f91df60a046b07`.
- REA 6.7.0: análisis estático de texto inerte; no se ejecutó el bundle.
- Inventario completo: 21.346 entradas, incluidas 15.251 de JavaScript.
  El inventario incluye dependencias, iconos y traducciones; su número no equivale
  a funciones de producto.

La pasada general analizó 3.082 archivos JavaScript, visitó 13.528.833 nodos AST
y conservó 42.547 observaciones. La reconstrucción de relaciones es **parcial**:
un analizador agotó su memoria y la expansión de relaciones alcanzó el presupuesto
de resultados. Se recuperó la evidencia retenida después del tiempo máximo de la
llamada, sin repetir la pasada general. Se hicieron inspecciones focales de los
puntos de entrada y de las familias funcionales para reducir los huecos.

REA observó siete contradicciones entre metadatos de integridad y bytes de
componentes nativos desempaquetados. Se registraron con `record-and-continue` y
esos bytes se trataron como no confiables. No se determinó su causa ni se dedujo
que fueran vulnerabilidades. La inspección del framework encontró activados la
integridad de ASAR, la carga exclusiva desde ASAR y el cifrado de cookies; Node
CLI inspect, `NODE_OPTIONS` y RunAsNode estaban desactivados. Son propiedades
estáticas, no una prueba de autorización o seguridad en ejecución.

No se declara una revisión exhaustiva del comportamiento de toda la aplicación:
las relaciones dinámicas, módulos omitidos, servicios remotos y comportamiento
en ejecución siguen fuera de lo demostrado. Un nombre de archivo o propiedad
solo sirve para localizar una superficie, no para atribuirle un comportamiento.

### Evidencia retenida

| Inspección | Identificador de evidencia REA |
| --- | --- |
| Pasada general | `ev_1ce03c6b4f7245e60dbe0783360890ee79a020b8677add0e04af53f02d5e15d5` |
| Aprobaciones, recuperación y estado: 12 rutas, 11 JS analizados | `ev_86096d727269b93132b1624b57e906ca4632256d755cb202150c6727603a1a43` |
| Entradas main, app-initial y app-shared; cobertura parcial | `ev_22403d77f48332da318989c0c21cd52ecc3f06ad3edbff2284bcd81d7ce5c5fe` |
| 30 módulos de las familias funcionales; 30 JS analizados, sin fallos de análisis sintáctico | `ev_511f7c08eb2305e98d3b79488ddd818000c31a6cb97d95e7c39d2989b86042e0` |
| Browser preload y app-shared aislados; límites del analizador, ambos omitidos | `ev_c40444783c1a936fcf1f8b3352099d77fe8a2c335f903725a8676e78260430ab` |
| Fuses del framework Electron | `ev_f869970471c0be6c491d5dbcf599f799c3b5067af466f16361b5ebd2335f82a7` |

El bundle de 16 registros se conserva fuera del repositorio en
`/private/tmp/codex-rea-evidence-final-20261022.json` (1.196.381.001 bytes).
Los identificadores permiten inspeccionar la sesión REA; no son enlaces públicos.
El inventario completo de rutas y tamaños también está fuera del repositorio.
El [manifiesto de cobertura](CODEX_REA_COVERAGE.json) registra las 30 rutas focales
y los huecos concretos. No se publica contenido propietario como parte de las
pruebas de Jarvis.

La pasada de 30 módulos leyó 1.644.141 bytes y visitó 492.832 nodos AST. El análisis
sintáctico completó esas rutas; las relaciones semánticas siguen siendo parciales
por dependencias no seleccionadas, llamadas dinámicas y límites explícitos del
grafo. No se suman sus cifras a las de la pasada general como si fueran archivos
distintos.

La inspección aislada de `.vite/build/browser-page-preload.js` y
`webview/assets/app-shared-60f4bb85435f.js`, con 2 GiB de memoria y 300 s por fuente,
tampoco pudo analizar esos dos módulos. Se leyeron sus bytes y se conservaron
identidad y límites, sin atribuirles resultados semánticos. No se presenta un
contador de cero hallazgos como ausencia de comportamiento o problemas.

## Contraste por familias

Jarvis registra 44 herramientas propias: 28 básicas y 16 de navegador por tarea,
dispositivos, programación, objetivos, habilidades e instalación de complementos.
A ellas se suman herramientas dinámicas de MCP y complementos. Las familias
siguientes cubren ese catálogo y las superficies de interfaz que las acompañan.

| Familia examinada | Superficie de Jarvis | Resultado de la revisión |
| --- | --- | --- |
| Chat, compositor y adjuntos | `JarvisChatStore`, envío, cola, corrección y reintento | Borradores por conversación, límite de memoria, aislamiento frente a respuestas tardías y envíos ajenos al compositor. |
| Aprobaciones y permisos por app | Broker, firma IPC, `ActionReviewView`, recibos de alcance | Aviso persistente cuando no se acredita el alcance solicitado; un fallo posterior del modelo no lo oculta. |
| Control del ordenador | `computer_use`, percepción, postcondiciones y diario de recuperación | Se distingue un fallo de visión anterior a cualquier acción de un resultado incierto después de intentar actuar. |
| Resultados nativos | Mail, Calendario, Recordatorios, Contactos, apps, navegador, música y Atajos | Recibos estrictos, ligados a la solicitud, y separación entre aceptación de comando y objetivo comprobado. |
| Terminal, sistema y archivos | Plantillas permitidas, estado, Spotlight y lectura de texto | Se conserva la ejecución acotada, las aprobaciones y los contratos existentes; no se añade una consola arbitraria. |
| Navegador | Panel integrado, `browser_task_open/action/close`, apertura y búsqueda | Se conserva el acceso vinculado a solicitud, origen y observación. Abrir una página no acredita completar el objetivo. |
| Proyectos | Instrucciones, archivos y conversaciones | Páginas autenticadas sin el corte de 50 chats ni la dependencia de los primeros 1.000 chats globales. |
| Automatizaciones y objetivos | Programación, Dot, aprobaciones con vencimiento | Se conserva el ciclo de aprobación y cancelación; la evidencia de ejecución no se convierte en una afirmación de efecto externo. |
| MCP, habilidades y complementos | Registro, esquemas, instalación y revocación | Se mantienen las fronteras existentes de argumentos, confirmación, identidad y destino; no se importan complementos de Codex. |
| Voz | Sesiones, adopción de conversación, VAD y permisos | Se protege el borrador al crear un chat por voz y se distingue de reanudar una conversación existente. |
| Privacidad y diagnóstico | Historial cifrado, temporales, registros y avisos | Borradores solo en RAM; errores de automatización acotados sin texto privado del sistema; registros de aprobación sin contenido del chat. |
| Dispositivos | TV, Android e iPhone | TV rechaza respuestas negativas o ambiguas; un acuse de envío no demuestra que cambió el estado del televisor. Se conservan las fronteras de Android/iOS. |

En la evidencia focal de Codex se observaron campos de alcance y riesgo de
aprobación, operaciones de consulta y revocación de permisos por app, identidad
de cuenta/conversación en recuperación y estados de automatizaciones. Se tomaron
como criterios de contraste. Los módulos llamados `draft-state` y `saved-drafts`
también contienen borradores de agentes o correo; no se usan como prueba del
comportamiento del compositor de chat. El diseño del caché de Jarvis procede de
sus propios requisitos y regresiones.

## Cambios y comprobaciones específicas

### Borradores y proyectos

El compositor tiene una única fuente de estado. Cambiar A → B → A conserva texto
y adjuntos de A si tiene ID, no es temporal y cabe en los límites del caché.
Un chat nuevo sin ID descarta su borrador al abandonarlo.
Solo el envío que consume ese compositor puede vaciarlo;
reintentar, enviar una cola o responder a un plan no debe borrar un borrador nuevo.
Los chats temporales no conservan su borrador al abandonarlos. El caché de
conversaciones inactivas tiene un máximo de 20 entradas y 4 MiB de contenido
contabilizado, con aviso visible si debe descartar contenido; no escribe borradores
en disco. Ese límite no incluye el compositor activo ni equivale a la RAM total
del proceso.

La paginación de proyectos ordena por fecha e identificador y usa un cursor HMAC
vinculado al propietario, proyecto y vida del servicio. Comprueba pertenencia,
papelera y temporalidad sobre registros autenticados. La interfaz rechaza
respuestas tardías, cursores inválidos y duplicados, y conserva la página previa
si falla una ampliación. No es una instantánea congelada: si un chat cambia de
fecha mientras se pagina, hay que pulsar «Actualizar» para volver a situarlo.

Pruebas: `ChatDraftRetentionTests`, `ProjectConversationPaginationTests` y
`test_project_conversation_pagination.py`, con datos sintéticos, empates de fecha,
123 chats de proyecto y más de 1.000 chats globales ajenos.

### Recuperación sin repetir acciones inciertas

`ComputerVisionNotDispatchedError` solo puede producirse antes de cualquier
intento de acción del controlador. El diario registra `not_dispatched` mediante
una actualización condicionada al mismo intento en ejecución. Una recuperación
explícita vuelve a pasar por broker y permisos; no reintenta por su cuenta. Si
falla la persistencia, conserva la incertidumbre y termina el trabajo con error,
en vez de dejarlo ejecutándose indefinidamente.

Después de intentar una acción, una respuesta incompleta de NVIDIA conserva la
incertidumbre. Guardar una explicación en el chat no borra esa evidencia. También
se unifica el análisis de «añadir», «agregar» e «insertar», conservando la exclusión
de instrucciones negadas o citadas y la verificación literal de la postcondición.

Pruebas: `test_computer_incomplete_vision_ipc.py`, `test_computer_postconditions.py`,
`test_computer_textedit_request.py` y regresiones de jobs/grafo. El IPC y el
controlador usan fixtures sintéticos; esto no prueba disponibilidad de NVIDIA.

### Recibos y errores nativos

Diez herramientas de mutación nativa exigen campos exactos, booleanos reales,
límites, fuente y correspondencia con los argumentos autorizados. JSON duplicado,
NaN, `{}` o una confirmación de otro objetivo no acreditan éxito. Los recibos
válidos indican `goal_verified=false` y el alcance
`native_command_acknowledgement`; métricas y recuperación conservan esa distinción.
Una invocación aceptada puede terminar el paso sin afirmar entrega de correo,
existencia final del evento, ventana visible o efectos internos de un Atajo.

Los programas JXA fijos devuelven códigos acotados para permiso denegado, espera
agotada, destino ausente, ambigüedad, límite de salida y respuesta inválida. Un
fallo genérico ya no se presenta siempre como falta de permiso. No se exporta el
mensaje privado de la excepción. Si puede haber habido una mutación antes del
error, la respuesta pide comprobar el resultado antes de repetir la acción.

Pruebas: `test_native_receipts.py`, `test_automation_errors.py` y contratos de
ejecución, presentación, recuperación y reflexión. La prueba de error posterior
a crear un evento usa una `Application` simulada en Node; no crea eventos reales
ni concede permisos TCC.

También se ejecutaron tres comprobaciones sintéticas del envoltorio con
`osascript -l JavaScript`: resultado correcto, código numérico de permiso y
destino ausente. Pasaron en el motor JXA de este Mac, sin llamadas a `Application`,
Apple Events ni datos del dueño. La denegación TCC real de estas apps no se simula
como si hubiera sido observada.

### Televisores y reintentos

WebOS exige `returnValue is True`; una respuesta vacía, negativa, numérica o de
otro tipo ya no se convierte en éxito. El JSON rechaza claves duplicadas y valores
no finitos, y el sobre WebOS exige el tipo de respuesta de comando y su ID.
El servicio y los consumidores aceptan
solo los acuses conocidos y conservan `goal_verified=false` con alcance
`smart_tv_command_acknowledgement`.

La recuperación de TV exige `target_device_offline` junto a evidencia explícita
de que la orden no se envió. WebOS y Tizen marcan el intento antes del envío: un
timeout posterior no activa Wake-on-LAN ni repite la orden. Android TV y UDP no
aportan esa evidencia de ausencia de envío y sus errores se detienen. Las pruebas
de `test_smart_tv_receipts.py` usan transportes simulados y verifican que los
fallos ambiguos no repiten la mutación; no se operó ningún televisor real.

### Permisos y comprobaciones manuales

`AppGrantReceiptTests` comprueba que un fallo del recibo de alcance permanece
visible después de un error del modelo. Los registros nativos contienen solo
el alcance y estados de aceptación/rechazo, sin texto ni identificadores de chat.

La instalación anterior `893291f` escribió y verificó «PRUEBA JARVIS C» en el
documento sintético de TextEdit. Los intentos posteriores terminaron con respuesta
incompleta del proveedor y no cierran la prueba del alcance. P1-5 sigue abierto
hasta completar en vivo conversación, «Siempre» y revocación. P1-12 mantiene
pendiente el recorrido auditivo con VoiceOver. Developer ID y actualización
notarizada siguen dependiendo de la membresía del dueño.

## Límites funcionales

La revisión mejora las herramientas que Jarvis ya tiene; no demuestra paridad
con cada función o servicio de Codex. La búsqueda del historial sigue centrada
en títulos; el navegador integrado conserva su superficie actual; el historial
general de acciones por conversación continúa como mejora posterior a la 1.0.
La ejecución real de dispositivos, correo, calendario y servicios externos exige
su configuración y las decisiones de privacidad o aprobación del dueño.

## Reproducción de la verificación

```bash
.venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/python -m pytest -p no:warnings
./script/test_native.sh
.venv/bin/python script/sync_localizations.py --check
./script/aegis.sh acceptance-benchmark
./script/aegis.sh production-workflows
./script/aegis.sh long-horizon-reliability
```

Las pruebas de IPC necesitan poder crear sockets locales. La primera ejecución
dentro del sandbox falló por `Operation not permitted` al enlazarlos y se detuvo;
esa pasada no se cuenta como calificación. La batería se ejecuta fuera del sandbox,
como el gate de publicación y Xcode. Los fixtures permanecen aislados de los datos
del dueño.

Los hooks vuelven a comprobar Python, Swift, paquete Release, aceptación,
producción y fiabilidad antes del push. El escenario sintético de producción usa
ahora recibos y argumentos coherentes, comprueba el alcance de aceptación y
rechaza acuses vacíos o una afirmación falsa de objetivo verificado.

Después del push se reinstala con `script/menu_bar_service.sh install`. La firma
requiere que el dueño escriba la contraseña del llavero directamente en Terminal.
La revisión instalada, firma estricta, tamaño del ejecutable, diagnóstico y VAD
sintético se comprueban sobre el bundle resultante; la compilación por sí sola
no acredita esa instalación ni las pruebas manuales pendientes.
