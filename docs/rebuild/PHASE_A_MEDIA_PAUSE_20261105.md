# Punto de continuación: medios y Codex — 5 de noviembre de 2026

**Pausado por petición del dueño.** No iniciar pruebas, compilaciones,
montajes, inferencia ni cambios de producto hasta que pida continuar.
Este archivo es un checkpoint local; queda pendiente de commit junto al
próximo cierre. No se publica ahora para no iniciar otra CI durante la pausa.

## Petición vigente y restricciones

Cerrar la validación macOS y el recorrido nativo de documentos/visión,
siguiendo mediante REA el reparto de responsabilidades de OpenClaw. Añadir
regresiones, validar/compilar en CI y publicar en `main`. La fase B va después.

El dueño permite instalar herramientas y dependencias necesarias. Su
restricción es evitar crecimiento descontrolado del almacenamiento durante
las pruebas. Las suites y compilaciones completas siguen en CI; cualquier
selección local necesita cuota, plazo, supervisión y recibo. No ejecutar
directamente `pytest` ni `swift test`. Aprobaciones de operaciones dentro de
perfiles sintéticos delegadas al agente; contraseñas, cuentas y permisos TCC
siguen a cargo del dueño. No leer `auth.json`, historial personal ni perfiles
anteriores, ni modificar su app instalada.

## Código terminado y publicado

Árbol limpio antes de añadir este checkpoint. `main` y `origin/main`:
`dfc3c345fa9eee9ba65f79b113da87405bacced5`.

- `0f1ec51`: preparación propia de texto embebido y vistas aprobadas, sin
  depender del OCR Apple para imágenes, PDF o muestras de vídeo.
- `dfc3c34`: prueba del orden de regiones con clases de color distinguibles;
  corrige un supuesto RGB demasiado estricto sin cambiar los píxeles.
- `f6b0bf8`: comparación de entornos y seguimiento REA del extractor PDF de
  OpenClaw; conserva también el historial del contrato OCR retirado.

Contrato actual: `embedded_text_and_visual_v1` obligatorio en solicitudes y
resultados visuales. Imagen/PDF/vídeo requieren JPEG válido, hash y regiones
asociadas a sus citas. Imágenes y vídeo no inventan texto extraído localmente;
PDF conserva el texto embebido por página. Codex interpreta los píxeles tras
aprobar el archivo exacto. La transcripción sigue siendo local y no envía
audio al proveedor. Hay rechazo explícito del host antiguo, frame ausente,
sobrepaso de tamaño, política incorrecta y resultados OCR antiguos.

Archivos principales: `NativeMediaExecutor*.swift`,
`NativeMediaDiagnostics.swift`, `AccessStore.swift`,
`src/jarvis_core/media_tools.py`, `media_documents.py` y sus regresiones.
Detalles: [PHASE_A_MEDIA_VISUAL.md](PHASE_A_MEDIA_VISUAL.md).

## CI exacta aprobada

[Suite completa de dfc3c34](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/41906098391):

- macOS: **4210 Python aprobados**; **724 Swift ejecutados, 1 omitido,
  0 fallos**. Compilación, bundle, helpers, exportación QA, paquetes e
  instalación de prueba aprobados.
- Ubuntu: **4163 aprobados, 47 omitidos**; paquetes e instalación aprobados.
- Entorno macOS: 27.0.1 arm64, SDK 27.0, Xcode 27.1 (27A9275), Swift 6.4.

[Diagnóstico focal de dfc3c34](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/41906295546):
**25 tests, 0 fallos**, 1,427 s; supervisor 125,757 s, pico 953.896.960 bytes,
cierre y fuentes sin cambios confirmados.

Se conserva el primer diagnóstico fallido de `0f1ec51`, run `41905078639`:
fallaron dos aserciones de color del nuevo oráculo, corregidas en `dfc3c34`.
No fue un fallo del PNG/PDF escaneado ni se cambia su resultado histórico.

Artefacto QA de CI: ID `13360806367`, 7.931.895 bytes; ZIP SHA-256
`32df57b22a3341bfbc81344c975dff0c2667b851ddeba0d4b0c28159dd63dc5d`.
No se descargó el bundle grande de 299 MB. Registro de entorno descargado en
`/private/tmp/jarvis-product-ci-20261104/dfc3c34-environment.json`, SHA-256
`10a9023c933e0ea664e52c8986091a09b122b3dca829db5b31018f636f236d35`.

Último focal local: 78 aprobados y 2 de codec reservados a CI; 3,15 s,
supervisor 4,087 s, pico 3.862.528 bytes. Recibo `dlxlti0p`, SHA-256
`d377bd9785b79556531d2216a8f04632421fd9115f51d553abe7f5745742ea66`.
Archivo y recibos anteriores en
`/private/tmp/jarvis-product-ci-20261104/local-receipts/`.
No se instalaron nuevas dependencias locales durante este seguimiento.

## Investigación REA acreditada

Referencia de lectura, sin modificar ni ejecutar:
`/Users/guillerjr/Documents/Proyectos/Jarvis-Base-OpenClaw`, revisión
`1e3ff306abaff29f03e7668d53b01c07f10294ac` (2026.10.7).

El seguimiento OC-26 comprobó fuentes inertes del plugin PDF: texto por página
con `clawpdf` y renderizado de páginas para modelos con visión. No requiere
OCR Apple. Jarvis implementa ese reparto con código propio; no importa ni
incorpora OpenClaw, `clawpdf` o su runtime.

Padre REA:
`ev_fe7be8c0f4004891b7c2345aebb30c60448bbb52566ee0c2ebbbaf69aa149d25`;
traza:
`ev_0fe17eb28c8510acc3dd704da1626c4feb0374885c5b8b5ccdcd2ad2c3b894ee`;
vista de módulo:
`ev_1c400bc5cea1bcec69cbcb7476997e13d50bd7a3fc1c071707a030e11c81c2e5`.
Esto acredita la partición seleccionada de fuentes, no ejecución del motor
PDFium ni revisión exhaustiva de OpenClaw. Ver
[REA_OC26_PDF_ENGINE_FOLLOWUP.md](REA_OC26_PDF_ENGINE_FOLLOWUP.md).

## Prueba real Q: fallo conservado, no cualificada

Caso: `/private/tmp/jarvis-media-visual-native-20261104-q`.
La app exacta de CI arrancó con el proveedor sintético previamente conectado.
Se importó un único PNG propio, se abrió «Prueba visual Q» y se inició una
sola ejecución. El modelo terminó **antes de que apareciera una solicitud de
herramienta o aprobación**. No se demostró recepción/consumo de los píxeles.

El modelo dijo que no pudo iniciar la herramienta, pero esto es texto del
modelo: no identifica por sí mismo la causa. En la base propia del caso no
hubo eventos `tool.*`, `approval.*` ni recibos de acción. El run figura
completado con respuesta del modelo, sin error propio de herramienta.
Todavía no se ha establecido si hubo un problema de protocolo del cliente,
enrutamiento o una llamada mal formada/no realizada por el modelo.

- Run Jarvis: `53d83e1d5a9341c5b7f93f8c535ce76f`.
- Sesión Jarvis: `0621cae89cea4ba4b2df255ad3c104e7`.
- Thread oficial sintético: `01a1a859-099e-7871-82e7-827a27d5de3b`.
- PNG: 1.961 bytes, 600×300; SHA-256
  `8b9c5100befa2e3f41db4b0ed8b1d78b37a346bbad7e711c8d9b62fd789e4f98`.
  No contiene texto. Oráculo fijado antes de inferencia: triángulo rojo a la
  izquierda, círculo verde central y cuadrado azul a la derecha.
- Oráculo revisado:
  `/private/tmp/jarvis-media-visual-q-oracle-reviewed-20261104.json`, SHA-256
  `ee1181b197b2f84205cdf96effd599dd951cbeb8c8265b5f2b80e4ea6fe39134`.
- `visual-result.json`: fallo `Q_run_ended_before_observed_bound_approval`,
  11,606 s, 40 RPC, 5.837 bytes recibidos, un upload y un `runs.start`,
  ninguna aprobación ni repetición automática. SHA-256
  `7b585aa9465249832e66969fb92fb738fc12326b46a56a0cbee6900df6523315`.

El supervisor cerró el caso con su plazo original de 810 s, mediante SIGTERM
al proceso propio identificado; **no fue cierre normal por la interfaz**.
Las cuatro identidades conocidas quedaron ausentes, lease libre y cuota
desmontada normalmente, sin forzar ni borrar originales o perfiles.
`closed.json`, SHA-256
`d20691c51ca65d43599cd1117f8a94222b83f774632a3e4f28f9e7e9115c7c49`.
La parte CUA terminó con su resumen de cierre.

**No volver a ejecutar Q, sus intents o su escenario. No usar `getApp` sobre
su ruta cerrada: podría relanzarlo.** Es evidencia fallida, no autorización
para reproducir automáticamente una operación.

## Almacenamiento y custodia al pausar

La imagen de cuota se conserva desmontada:
`/private/tmp/jarvis-product-background-native-20261104-e/quota.sparseimage`.
Mount anterior: `/private/tmp/jq-8k57q6f0` (cerrado, modo 000).
Imagen identidad dev/inode `16777230/72850467`; mount subyacente
`16777230/72850465`. Son datos de custodia históricos, revalidar al continuar.

- Cuota original: 2 GiB; pico utilizado 713.949.184 bytes.
- Imagen física al cierre: 778.047.488 bytes; crecimiento conocido de Q
  38.797.312 bytes.
- Proveedor sintético: baseline original 170.102.784 bytes; límite absoluto
  237.211.648 bytes. Pico de crecimiento 4.255.744 bytes. No reiniciar baseline.
- Pico de descenso global no atribuido: 69.394.432 bytes, por debajo del
  límite de 1 GiB; sin fallo de recursos. Última muestra Data disponible
  207.634.915.328 bytes. Son muestras históricas, no medición actual.
- No hay suite, compilación o supervisor Q en curso. Ninguna operación
  forense ha montado el volumen después de Q.

## Continuación exacta, cuando lo pida el dueño

1. Revalidar `git status`, HEAD y custodia; no repetir la suite ya aprobada
   para `dfc3c34`. Leer este checkpoint y los recibos Q antes de actuar.
2. Investigar el fallo de despacho anterior a medios. Contrastar el esquema
   **del cliente oficial fijado 0.163** con `src/jarvis_core/codex.py` y sus
   tests. Se confirmó `experimentalApi=true`, `dynamicTools` en `thread/start`
   y rechazo de solicitudes con `namespace` no nulo. Esto aún no demuestra
   la causa. `mcp_registry.model_visible()` conserva visibles las herramientas
   propias; no se ha probado un filtrado de `media_inspect`.
3. Si se necesita evidencia de runtime, preparar una admisión forense nueva,
   acotada y de sólo lectura de **ese thread sintético Q**, bajo
   `provider/client/sessions` dentro de la imagen conocida. Excluir siempre
   `auth.json`, otros threads e historial personal. No ejecutar inferencia ni
   reusar el escenario Q. Aún no existe el script/plan forense solicitado.
4. Establecer la causa con evidencia; corregirla y añadir regresiones del
   protocolo real sin debilitar la autoridad de acciones ni admitir namespaces
   arbitrarios. Publicar y validar el nuevo SHA en CI.
5. Sólo después, admitir un caso nativo **nuevo** con límites originales,
   fixture/oráculo previo, una aprobación y una ejecución. Verificar entrega
   única de la vista y respuesta que dependa de sus píxeles. Cerrar enseguida
   por la interfaz y verificar procesos/lease/cuota; no dejarlo esperando.
6. Actualizar documentos con CI verde, Q fallida y el resultado nuevo sin
   convertir preparación/ACK en consumo verificado. El texto de cualificación
   de `PHASE_A_MEDIA_VISUAL.md`, README y V1 todavía precede a la CI verde y
   a Q. Commit/push de cierre en `main` cuando corresponda.

## Seguimiento del 5 de noviembre: diagnóstico de Q, sin inferencia

Reanudado por petición del dueño. No se montó la cuota de Q, no se leyó su
thread, no se ejecutó inferencia ni se repitió el escenario. El esquema del
app-server se generó con Codex 0.163.0 y un `CODEX_HOME` vacío, sin cuenta.

Descartado con código y esquema: la forma de `dynamicTools` coincide
(`type: function`, `inputSchema`) y `media_inspect` se ofrece en cada run. Un
`item/tool/call` rechazado por namespace, turno o nombre habría dejado igualmente
`tool.reply.ack` al completarse su item; Q no tuvo eventos `tool.*`.

Hallazgo: Jarvis respondía `-32601` a solicitudes nativas de Codex e ignoraba
los items nativos completados sin registrar nada, de modo que un intento del
modelo con una herramienta propia de Codex era invisible. Además, 0.163 tiene
activas por defecto `unified_exec`, `computer_use`, `browser_use`,
`browser_use_external`, `in_app_browser`, `tool_suggest` y `sleep_tool`, que
Jarvis no desactivaba. No se verificó cuáles ve el modelo en el app-server:
`codex debug prompt-input` no varía con esos flags y no sirve de prueba.

Corrección: eventos `provider.request.rejected` (sólo método y motivo, o
hashes para una llamada dinámica mal ligada) y `provider.item.native` (sólo
tipo y estado, sin comandos, rutas ni salida), y desactivación de esas siete
funciones. Las conversaciones y los propios `dynamicToolCall` no generan
eventos nuevos. `code_mode_host` y `goals` siguen sin tocar: no se ha
establecido si las herramientas dinámicas dependen de ellas.

Selección focal: 100 casos de `tests/test_codex_tools.py` y `tests/test_codex.py`
aprobados en 30,492 s, pico 5.431.296 bytes, limpieza confirmada; recibo
`/private/tmp/jarvis-test-resource-501-68a1d7e5af41/jarvis-test-evidence-vdng0vvv/receipt.json`,
SHA-256 `5b2008880e42bdc7bfc9c99779aeb262434774fb0dd90aedccce79bb6ea962cf`.
No es suite completa. La [CI completa de `8827301`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/42074833787)
terminó **fallida**: 8 fallos en `tests/test_codex_cleanup.py` en Ubuntu
(4157 aprobados, 47 omitidos) y macOS (4204 aprobados). Las constantes nuevas
se insertaron entre el docstring de `CodexError` y su `__init__`, que dejó de
aceptar `cleanup_stage`; la selección focal no incluía ese archivo. Corregido
en el commit siguiente. Focales tras la corrección: 30 casos de
`test_codex_cleanup.py` y `test_codex.py` en 10,228 s (recibo SHA-256
`1387b1f7238f04a771df39f86f7a6fe692b797ac69e1c2f6d938b8fbb3e179fb`) y 84 de
`test_codex_tools.py` en 22,226 s (recibo SHA-256
`5f4468e8a726dccade510240443cbd3215a9bb3cd22e905d3a25fc0ae86f9af1`), ambos
con limpieza confirmada. La causa de Q sigue **sin establecer**: la corrección
sólo hace observable el próximo intento. Falta la CI del nuevo SHA y un caso
nativo nuevo; la lectura del rollout Q sigue requiriendo autorización del dueño.

La [CI completa de `613f210`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/42080268038)
terminó en verde: 4165 Python en Ubuntu (47 omitidos), 4212 en macOS y 724 Swift
(1 omitido, 0 fallos).

## Caso nativo R: Q reproducido, cierre normal, no cualificado

Caso nuevo en `/private/tmp/jarvis-media-visual-native-20261105-r`, encadenado a
los recibos de Q y ligado a `613f210` y su artefacto nativo 13441410101. Fixture
nuevo `visual-r.png` (2102 B, SHA-256
`0dbdf66414de9d57c0b1f8bf6c99e9a5579ce56c39741dc01389d5f78c5206b2`) y oráculo
oculto fijado antes del modelo; mismo prompt que Q. Admisión
`d0d402e52096c6e2b4cf04a0fb00491cf7742a0baa3cef4a54ef9debd4d7b472`, preparación
`24f1aff6d4f5b1129c8119fe56e3182d878212d32186ce50b8fffc693d9824b3`.
Un primer comando de lanzamiento mal formado (entrada `prepare.py` con argumentos
del supervisor) fue rechazado por `argparse` antes de cualquier efecto.

Chat «Prueba visual R» renombrado por la interfaz (menú contextual, control de
pantalla puntual), sesión `5ddfd29c91fb4529a5097afba5accace`, leída con un único
`sessions.list` de sólo lectura. Un import, un `runs.start` y ninguna aprobación:
`R_run_ended_before_observed_bound_approval` en 12,293 s, 43 RPC. Resultado
`visual-result.json`, SHA-256
`d6f987dccf811124e409c3ef57d668b13ccdd32fd0ad1fbd43144ab978bd72ef`.

El run quedó completado/conocido/limpio, sin recibos. El modelo escribió que
inspeccionaría el adjunto y después `{"figuras":[],"error":"No recibí la imagen
porque falló la herramienta de inspección."}`; es texto del modelo. Diagnósticos
recogidos: **ninguno** (`provider.request.rejected`, `provider.item.native` y
`tool.reply.*` ausentes). Una lectura posterior de sólo lectura de todos los
eventos del run sólo mostró `run_queued`, `media.references`, `run_status`,
deltas, **dos `usage.update`** separados entre los dos mensajes, `usage.final` y
`run_settled`. El segundo paso de inferencia indica que hubo una llamada de
herramienta dentro de Codex cuyo resultado volvió al modelo sin `item/tool/call`
hacia Jarvis ni item completado de tipo no conversacional. La causa sigue **sin
establecer**. Hipótesis sin verificar: invocación dentro de un modo de código de
Codex (`code_mode_host` sigue activo) o rechazo interno de argumentos antes del
despacho. Establecerla requiere leer el rollout del thread sintético R, que
necesita autorización del dueño.

Cierre: intención GUI registrada y «Quit» del menú; `closed_after_GUI_intent`
en 186,857 s, sin señales ni incidencias, cuatro identidades ausentes, mismo lease
libre, cuota desmontada al primer intento sin fuerza; imagen física
815.796.224 bytes; pico residual no atribuido 21.508.096 bytes. `closed.json`
SHA-256 `f640ad9ecb823357e43abd71d0cb69ea1b83f2163ed7443458fdcf693faedd4a`.
R no cualifica medios ni la fase A. No volver a ejecutar Q ni R.

### Causa establecida con el rollout de R

Con autorización del dueño, la imagen de cuota se montó en **sólo lectura** en
un punto nuevo y vacío. Se leyó sólo `state-r/jarvis.sqlite3` (modo inmutable)
para obtener el thread `01a1ad00-2547-7f33-88c3-44244d220696` y su rollout
`provider/client/sessions/2026/11/05/rollout-2026-11-05T13-37-10-01a1ad00-2547-7f33-88c3-44244d220696.jsonl`
(94.513 B, SHA-256 `d6639fe5edeb00d04c0caea902409510e052d839317582c86a3eccd5e03325da`).
No se leyó `auth.json` ni otro thread. Desmontaje normal; imagen sin cambios
(815.796.224 B, mismo inodo) y montura canónica desmontada con modo 000.

El thread recibió las 55 herramientas dinámicas, incluida `media_inspect`. El
modelo `gpt-6.1-sol` no la llamó directamente: usó la herramienta propia de modo
de código `exec` (`custom_tool_call`) con JavaScript que invoca
`tools.media_inspect({...})` con los argumentos exactos. Su salida fue
`failed to spawn code-mode host /private/tmp/jq-8k57q6f0/bin/codex-code-mode-host:
No such file or directory (os error 2)`. Después el modelo dio la respuesta de
fallo. El app-server sólo emitió `item_completed` de `UserMessage` y
`AgentMessage`: la llamada `exec` no llega a los clientes como item, por eso
`provider.item.native` no la vio.

Causa: el arnés de QA copia a la cuota sólo `bin/codex`, no su binario hermano
`codex-code-mode-host` (65.643.072 B) que sí existe junto al Codex instalado y que
`script/package_desktop_runtime.py` ya exige en el paquete autocontenido. Q
presenta la misma firma y se atribuye a la misma causa sólo por analogía: su
rollout no se leyó. Además, el modo de código mete las herramientas dinámicas
dentro de un `exec` que Jarvis no observa ni autoriza como llamada propia hasta
que el host la despacha.

La [revisión REA OC-29](REA_OC29_CODEX_CODE_MODE.md) del arnés Codex de
OpenClaw confirma que la referencia nunca deja el modo de código a su valor por
defecto: lo desactiva en turnos acotados y de imagen y aparta del modo de código
las herramientas con imagen o aprobación. Jarvis no portó esa parte.

Por decisión del dueño se portó la rama desactivada de OpenClaw:
`CodexClient.start()` fija `features.code_mode=false`,
`features.code_mode_only=false`, `features.goals=false`,
`features.multi_agent_v2=false` y `agents.enabled=false`, y el peer sintético
de `tests/test_codex_tools.py` rechaza arrancar sin ellos. Pruebas focales
supervisadas verdes para `test_codex_tools.py`, `test_codex.py` y
`test_codex_cleanup.py`; la cualificación completa depende de la CI del commit.
Queda sin acreditar que Codex 0.163 retire `exec` con estos valores: requiere un
caso nativo nuevo (S) con la copia de QA actualizada. Q y R no se repiten.

### Casos S y T (`ba82724`, CI completa 42094785019)

S (`/private/tmp/jarvis-media-visual-native-20261105-s`, admisión `10ceff91…`)
falló en la preparación por `preparation_deadline`: la descarga del ZIP nativo
llevaba 2.948.036 de 7.931.686 B a los 300 s. No lanzó la app. Su recibo
(`preparation-failed.json` `afe035da…`) conserva por error del arnés las
etiquetas `prior_S_*` y `fresh_R_profile` con los hashes de R. La cuota se
desmontó después sin forzar, con intent y recibo propios (`detach-result.json`
`b50949ef…`); el ZIP parcial queda dentro de la imagen.

T (`/private/tmp/jarvis-media-visual-native-20261105-t`, admisión `d1f2820b…`)
repite S con el ZIP exacto descargado antes de la ventana (644 s, SHA-256
`737429cf…`) y verificado otra vez al copiarlo. Preparado `ae7644fa…`; chat
«Prueba visual T» creado por UI; sesión `4c0ff02717374f93a500c90e07a29de9`;
resultado `visual-result.json` `7d2287af…` con
`T_run_ended_before_observed_bound_approval` y sin diagnósticos del proveedor;
cierre `closed_after_GUI_intent` (`bb948598…`) sin señales.

El rollout del thread `01a1ad6b-429d-7881-9968-c9bf8dd574cd` (SHA-256
`e7ef86e4…`, leído sobre un montaje de sólo lectura) muestra que el modelo
volvió a llamar a `media_inspect` desde `exec` y que falló igual por falta de
`codex-code-mode-host`. `multi_agent_version` sí pasó a `disabled`, de modo que
los ajustes llegan a Codex. La causa: el catálogo de modelos cacheado declara
`tool_mode: "code_mode_only"` para todos los modelos ofrecidos, incluido
`gpt-6.1-sol`, y en 0.163 eso impone el modo de código aunque
`features.code_mode=false`. El binario expone las superficies
`code_mode_only`, `direct_model_only`, `deferred_model_only` y `hidden`, y la
tabla `features.code_mode` acepta `direct_only_tool_namespaces`: es el
mecanismo que usa OpenClaw (OC-29) para sacar del modo de código las
herramientas con imagen o aprobación. Desactivar el modo de código no basta;
Jarvis necesita namespaces sólo directos, lo que exige aceptar llamadas con
namespace en `item/tool/call`.

Por decisión del dueño se implementó: `thread/start` ofrece todas las
herramientas de Jarvis dentro de un único `{type: "namespace", name:
"jarvis_direct"}` (forma `NamespaceDynamicToolSpec` del esquema de 0.163), y
`CodexClient.start()` pasa `features.code_mode` como una sola tabla
`{enabled=false, direct_only_tool_namespaces=["jarvis_direct"]}`. Se cambia el
contrato: `item/tool/call` sólo se acepta con `namespace == "jarvis_direct"`
(una llamada de raíz o de otro namespace se rechaza), y el acuse
`dynamicToolCall` debe traer ese mismo namespace. Los dos fixtures de scripts
que simulan Codex se adaptaron. Pruebas focales supervisadas verdes para
`test_codex_tools.py`, `test_codex.py` y `test_codex_cleanup.py`; los tests de
los fixtures no pueden escribir en `/private/tmp` bajo el guard local (falla
igual sin el cambio) y dependen de CI. Falta un caso nativo nuevo (U) que
acredite que Codex 0.163 ofrece `jarvis_direct` como llamada directa a un
modelo `code_mode_only`.

### Caso U (`d937e55`, CI completa 42106136789)

U (`/private/tmp/jarvis-media-visual-native-20261106-u`, admisión `76ab4d0c…`)
se encadenó a T, copió el ZIP predescargado (4 s de descarga, SHA-256
`b7499749…`) y preparó a tiempo (`f30e1cfc…`). Chat «Prueba visual U»
creado por UI; sesión `866e3cfd87f4449396fe75a6cf01a215`; cierre
`closed_after_GUI_intent` (`d723362a…`) sin señales; la cuota quedó
desmontada.

El rollout del thread `01a1b0f7-71c7-7ec2-90e8-33ea0f9cd097` (SHA-256
`10180d1f…`, leído sobre un montaje de sólo lectura) muestra lo que Q, R y T
no lograron: una `function_call` directa a `media_inspect` con
`namespace: "jarvis_direct"` (sin `exec`), la aprobación ligada decidida una
vez, la respuesta de la herramienta y la vista aprobada entregada como
`input_image`. La respuesta final del modelo (fase `final_answer`) es el JSON
exacto de las tres figuras y coincide con el oráculo oculto: triángulo rojo a la
izquierda, círculo verde en el centro y cuadrado azul a la derecha. Los
diagnósticos registran `tool.reply.sent` con escritura confirmada y el acuse
`matched`.

Aun así el recibo del escenario (`visual-result.json` `e18bf157…`) es
`failed_or_unknown` con `JSONDecodeError` y
`synthetic_pixel_consumption_evidence=false`: Jarvis compuso la respuesta del
run uniendo el mensaje de fase `commentary` («Inspeccionaré el adjunto…») con
la respuesta final, y el oráculo exige JSON puro. No se reinterpreta el recibo
ni se repite U. Queda un defecto propio: `CodexClient.chat` ignora el campo
`phase` de `agentMessage` (`commentary`, `partial_answer`, `final_answer`), que
el esquema de 0.163 define y pide tratar como desconocido cuando falta.

Corregido después por decisión del dueño: la respuesta del run excluye los
`agentMessage` con `phase: "commentary"`; un mensaje sin fase se conserva y, si
el turno sólo trajera narración, se usa ésta en lugar de una respuesta vacía.
Los deltas en vivo (`answer.delta`) no cambian. Regresiones en el peer de
`tests/test_codex.py`; pruebas focales verdes para `test_codex.py`,
`test_codex_tools.py` y `test_codex_cleanup.py`. Hace falta un caso nativo
nuevo (V) para acreditar el consumo de píxeles con el recibo del escenario.

La documentación oficial consultada antes de pausar reconoce que
`dynamicTools` y `item/tool/call` son experimentales y exige nombres de tools
y namespaces compatibles con Responses. No se concluyó que esto explique Q:
[App Server](https://learn.chatgpt.com/docs/app-server#dynamic-tool-calls-experimental).
No se cambió proveedor ni se leyó una credencial para investigar.

El caso de verificación prolongada H/I queda separado y sin cualificar; Q no
lo cierra. Este trabajo tampoco completa todas las familias de fase A
(matriz amplia anterior: 62 parciales, 18 ausentes, 0 familias globales
declaradas completas). La fase B no se ha iniciado.

Subagentes avisados de detenerse: `local_xctest_resolution` y
`native_g_readiness`. Sin cambios de código de producto tras `dfc3c34`.
