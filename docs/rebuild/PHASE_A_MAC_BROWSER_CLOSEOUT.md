# Herramientas del Mac y navegador — cierre del incremento A

Este registro documenta la implementación propia del bloque solicitado, las
suites completas de sus checkpoints y las regresiones posteriores.
**Las acciones, el frame real y los recorridos nativos de concesión,
persistencia, revocación y rechazo del bundle 04 quedaron cualificados.
Este registro es el snapshot anterior al commit que lo contiene.**
No declara completa la fase A ni sustituye la instalación personal anterior.
El [registro JSON](PHASE_A_MAC_BROWSER_CLOSEOUT.json) separa esos estados y fija
las identidades de las evidencias disponibles.

## Implementación y conexiones

| Contrato implementado | Comportamiento y límite |
| --- | --- |
| [Parches de workspace](PHASE_A_MAC_PATCH_PTY.md) | Diff unificado UTF-8 multiarchivo: alta, edición, baja y movimiento, contexto y hashes exactos, rutas explícitas y workspace físico. Preflight de todos los archivos antes del primer efecto; publicación atómica por archivo. Un efecto parcial conserva `unknown`, archivos completados y fase incierta, sin rollback sobre cambios ajenos ni replay. |
| [Terminal PTY](PHASE_A_MAC_PATCH_PTY.md) | stdin, EOF canónico, salida/cursor, resize, SIGINT/SIGTERM, cancelación, límites y custodia de descriptores. Seatbelt real en macOS y fallo cerrado si falta. Sirve programas admitidos y REPL bajo confinamiento de **un solo proceso**, sin fork ni shell arbitrario. |
| [Observación y acciones del Mac](PHASE_A_MAC_AUTOMATION.md) | Bundle, PID, lanzamiento y ventana exactos; referencias AX de observación, texto/selección UTF-16, acciones AX y eventos dirigidos. Identidad pública de ventana estable, referencias caducables y revalidación de contenido/foco/lease antes del efecto. Input sin postcondición comprobada conserva incertidumbre. |
| Píxeles efímeros | Frame ScreenCaptureKit sólo de la ventana elegida, con AX completo y estable, controles protegidos o sin tipo retirados y JPEG de hasta 48 KiB en RAM. El canal de imágenes del proveedor es efímero; recibos, SQL e historial conservan metadatos, no el portador base64. AX y píxeles no son una captura atómica. |
| [Navegador administrado](PHASE_A_BROWSER_AUTOMATION.md) | Ventanas WebKit propias con almacenamiento no persistente, scripts fijos en mundo aislado y refs ligadas a origen/documento/snapshot. Navegación, historia, fill, selección, click, teclas finitas y cierre; sin código del modelo, selectores arbitrarios, cookies exportadas ni permisos automáticos. |
| Conexión CDP explícita | Endpoint loopback y `target_id` exactos, con validación del destino `page`; sin enumerar pestañas o perfiles. La conexión no aplica el proxy del navegador propio al navegador externo. Hay pruebas separadas de peer CDP sintético y de Chrome 155.0.8059.85 real con perfil nuevo propio. |
| Búsqueda y descarga | Plantilla de búsqueda elegida en el control del dueño, sin motor implícito. Descarga explícita del mismo origen, sin credenciales ambientales ni redirects, hasta 64 KiB; publicación autorizada en una ruta nueva del workspace modo 0600, con bytes/hash comprobados. `written_to_workspace` sólo pasa a verdadero después de esa publicación verificada. |
| Autoridad e interfaz | Una aprobación por operación, sesión, ejecución y argumentos; worker nativo firmado y lease de efecto de un uso. Ámbitos de conversación/«Siempre», temporales, revisiones CAS y revocación; cambiar la plantilla de búsqueda invalida permisos previos. El broker distingue claim, comprobación previa, respuesta y asentamiento; perder un ACK o cancelar no autoriza repetir. |

El catálogo productivo sigue la secuencia preparar → aprobar → ejecutar. Los
ejecutores y el helper de descarga no conceden otra autorización. Reset,
revocación, cancelación y pérdida de transporte retiran refs y preservan la
custodia mientras queden callbacks o efectos sin asentar. La siguiente ejecución
de la sesión respeta esa barrera.

El navegador propio combina proxy CONNECT autenticado sin fallback, selección
de IP pública y reglas de contenido obligatorias. Los orígenes HTTP loopback
de QA necesitan una excepción confiable exacta, incluido el puerto, declarada
como `synthetic_origin_exception=true`. El negativo del señuelo comprueba que
otro origen privado no recibió la petición; el tráfico de QA no se presenta
como navegación pública fijada por DNS.

## Investigación REA y trazabilidad

La referencia permanece en
`1e3ff306abaff29f03e7668d53b01c07f10294ac`, sólo de lectura. Se usó `git show`
para extraer blobs exactos a particiones privadas nuevas e inmutables, con
política de integridad estricta, sin ejecutar o modificar el clon y sin copiar
su implementación al producto.

Este incremento añade [OC-22A](REA_PART22A_SUMMARY.json),
[OC-22B1/B2](REA_PART22B_SUMMARY.json) y [OC-22C](REA_PART22C_SUMMARY.json).
Los cuatro lotes conservan manifiestos, blobs Git, SHA-256, Evidence IDs y
720, 271, 331 y 631 incógnitas semánticas respectivamente. El inventario
acumulado registra **27 lotes estáticos y 105 rutas TS/JS únicas** —103 si se
cuenta sólo TypeScript—. Es un recuento de fuentes seleccionadas, no cobertura
semántica ni de ejecución. Las dependencias y rutas no examinadas siguen
pendientes en [REA_COVERAGE.json](REA_COVERAGE.json).

La [matriz](OPENCLAW_BASELINE_PARITY.md) mantiene 80 familias: **47 avances
parciales con contratos probados y 33 sin implementación**. Esos estados no
son porcentajes de producto terminado ni cierres de familias completas. La
fase A continúa abierta; la fase B permanece aplazada.

## Comprobaciones y snapshots

| Control ya observado | Resultado y alcance |
| --- | --- |
| Workspace/patch/procesos/PTY/custodia | 151 casos Python aprobados; archivos reales y Seatbelt/PTY reales con decisiones sintéticas. Incluye denegaciones de red, fork y rutas fuera del workspace, REPL, EOF, señales, revocación y efecto parcial. |
| Broker y permisos | 52 casos Python aprobados; IPC Unix firmado y callbacks/decisiones de prueba: cliente/nonce/lease exactos, revocación, pérdida de ACK, limpieza pendiente, frames y permisos persistentes/volátiles. |
| Focal integrado Python del coordinador | 108 casos aprobados en la selección registrada. Se solapa con las anteriores; no se suma como otra cantidad de pruebas únicas. |
| Auditoría nativa Mac + navegador | 39 casos aprobados: 23 Mac, 13 navegador/CDP y 3 de auditoría. WebKit, AppKit y HTTP/proxy propios son reales; el driver AX, peer CDP y decisiones/callbacks de prueba son sintéticos. |
| Opciones confiables de QA | Seis casos aprobados: sólo raíz temporal privada y origen HTTP loopback exacto, sin mezclarlos con recuperación/restauración ni ampliar acceso a ventanas privadas del host. |
| Fixture de recorrido GUI | Corpus nuevo con sesión vacía, peer SDK propio y HTTP loopback artificial; 17 pruebas nuevas y cuatro históricas de compatibilidad aprobadas en el recorrido del subagente. Sus fuentes/hashes quedan fijados; no es una ejecución GUI ni inferencia autenticada. |
| Python completo actual | **2672 aprobadas, cero fallos, 488,93 s**. Ningún `.py` cambió después. El checkpoint anterior de 2670/598,94 s y su manifiesto histórico de 173 rutas se conservan; ahora los dos casos de descarga también están en la ejecución completa. |
| Swift completo antes de la última corrección AX | **392 casos, uno omitido y cero fallos, 154,796 s**. La omisión es Chrome opt-in, ejecutado por separado. Incluye lifecycle/CAS/ChatStore; no incluye la separación posterior de ACK/postcondición ni sus cinco tests nuevos. El checkpoint anterior de 382 se conserva. |
| Lifecycle nativo posterior | **Nueve casos aprobados, 12,653 s**: dos DTO, dos de host firmado/WebKit y cinco nuevos de concurrencia start/stop/reconnect con respuestas RPC retrasadas. |
| CAS semántico AX posterior | **31 casos aprobados, 5,365 s**: cinco nuevos de grupos nativos vacíos, 23 del backend Mac y tres de auditoría. Conserva la identidad física del editor y rechaza contenido semántico, reemplazos, padres inválidos, ciclos y duplicados. |
| ACK y postcondición AX posteriores a los 392 | **33 casos aprobados, 0,735 s**: cinco nuevos de ACK/postcondición, cinco CAS y 23 Mac. Un único despacho, lectura exacta autorizada y metadatos ACK separados; sin reenvío ni cambio del timeout nativo. No se anticipa un nuevo resultado global. |
| Chrome real independiente | **Un caso aprobado, 2,945 s**, con perfil temporal nuevo, página propia, attach exacto, fill/readback, click con oráculo HTTP, refs obsoletas, origen discordante, navegación y detach que conserva la pestaña. Cierre normal del Chrome propio y su servidor. |
| App nativa en ejecución, build 02 | **55 operaciones por RPC firmado**, con 55 propuestas y 55 respuestas del peer SDK propio. Son 52 runs terminados y tres negativos: dos denegaciones y una ref obsoleta sin segundo click. Incluye una única apertura HTTPS pública y su cierre. El error AX conocido de ese snapshot no se convierte retroactivamente en éxito. |
| SUT con TCC y fixture externo | **13 operaciones conservadas en un corte inmutable**: set_value Unicode/salto de línea, type_text con selección UTF-16, frame JPEG real de 14.300 bytes y descarga publicada con indicador correcto. Un press anterior sigue `unknown`, aunque el oráculo externo muestre contador 1; no se repite ni se contabiliza como éxito. |
| Bundle 04 y destino nuevo | Cinco operaciones verificaron resolver/ventana, aprobación requerida para el destino nuevo, un único press 0→1 y frame real de 12.352 bytes. `succeeded`, `verified` y postcondición fueron positivos, con `native_acknowledged=true` separado. El antiguo PID había salido y no se reutilizó. |
| Tarjeta nativa «Siempre» | Concesión CUA, persistencia tras reiniciar los mismos bytes, rebind nativo explícito, lectura recordada sin aprobación, revocación CUA y rechazo de solicitud nueva comprobados. El observador no envió decisiones RPC. |
| Bundle final de desarrollo | Build 03 compilado en **53,98 s**, plist y firma de desarrollo comprobados. El build focal de ChatStore también pasó, 13,26 s. Los efectos y permisos se acreditan con los recorridos independientes, además de compilar. |

El build 04 pasó en **45,88 s**. La identidad cualificada es
`com.jarvis.desktop.macbrowserqualification.v04.20261027`, CDHash
`d0fbc0dcd13bfcef08ddb9583fbbb03c81cc421e`, de las 356 fuentes actuales.

Los focales se solapan y pertenecen a snapshots sucesivos; **no se agregan en un
total global**. Los logs, sus hashes y los intentos corregidos se identifican en
el JSON. El focal nativo anterior de 22 casos y el de navegador de 13 se conservan
como checkpoints; no sustituyen el último focal de auditoría.

El [manifiesto de fuentes](PHASE_A_MAC_BROWSER_SOURCE_MANIFEST.json) conserva
sin alterar el manifiesto histórico Python de 173 rutas, el checkpoint de 355
y el actual de **356 fuentes, scripts y tests**, todos identificados. Las 356
rutas actuales se compararon con el árbol congelado; el manifiesto original v2
tiene SHA-256
`30ffc5274692415f79531d86c6de0efd135346f0ef26720714518cd24f5a54f2`.
Entre 355 y 356 sólo cambiaron tres archivos Swift y se añadió el archivo de
cinco tests de ACK/postcondición; no cambió Python.
Los documentos quedan fuera de esa identidad de fuentes. No se atribuye el
resultado de una suite anterior a un snapshot que aún no existía.

El recorrido build 02 usó la app nativa real y su worker firmado, WebKit real,
Chrome propio real, archivos reales y PTY/Seatbelt reales. El peer SDK y las
decisiones de aprobación fueron sintéticos. Se comprobaron aislamiento del
permiso de conversación, «Siempre» en otro chat y revocación por RPC; los clicks
del sitio se confirmaron mediante **GET `/confirm`**, una vez por navegador.
El patch cambió los archivos esperados, la descarga coincidió en bytes/hash,
la PTY quedó reaped y no quedaron portadores de imagen en SQLite ni archivos
de captura. Se retiraron permisos y bindings del navegador al cerrar ese
recorrido. Esos hechos RPC se mantienen separados del click nativo de «Siempre»
posteriormente observado por CUA.

La apertura pública fue únicamente `https://example.com/`, una vez, sin retry,
con `private_pinned_proxy` y `synthetic_origin_exception=false`; la pestaña quedó
cerrada. Su contenido conserva `external_unverified=true` y `verified=false`:
es una lectura real por HTTPS, no verificación del contenido ni de otros sitios.

La escritura AX Unicode/salto de línea de ese recorrido devolvió un error
conocido de CAS, con `succeeded=false`: no se cuenta como escritura verificada.
La comparación detectó crecimiento de grupos vacíos del chrome nativo. Su
corrección tiene cinco regresiones y ahora un positivo AX **entre procesos**
contra el fixture propio PID 27924. Sus objetos AppKit independientes confirmaron
texto Unicode con salto de línea y selección UTF-16: 23 unidades después de
set_value y 41 después de type_text.

El corte inmutable de 13 operaciones usa el SUT
`com.jarvis.desktop.macbrowserqualification.final.20261027`, CDHash
`bc7101b3af701d2841416321ff2c9b3b4a4b70ad`, del snapshot de 355 fuentes. Entregó
un frame real JPEG de **14.300 bytes**, SHA-256
`bf4463d26c31bfd1cd61360093d84d35b25dc89abcc4ee0d8ea42905c1cdde0c`.
El peer sólo conserva bytes/hash en el log; los oráculos no encontraron portador
en SQLite o el log SDK ni los archivos de captura de control. Los controles
protegidos quedaron retirados. La descarga de 21 bytes coincidió en archivo,
hash y `written_to_workspace=true`; se cerró su binding.

El press de ese mismo corte se despachó una sola vez: contador externo y lectura
AX nueva mostraron `CONTADOR 1`, pero el recibo conservó
`unknown/native_action_unconfirmed`. Ese recibo no se reescribe como éxito. La
corrección posterior separa ACK de postcondición observada y tiene cinco tests
con un solo despacho. Su positivo sobre el bundle 04 ya se comprobó en una
operación nueva; el recibo histórico incierto se conserva sin replay.

## Reanudación y cualificación del bundle 04

La [pausa](PHASE_A_MAC_BROWSER_PAUSE_20261027.md) es un antecedente histórico,
reanudado expresamente por el dueño. El fixture antiguo PID 27924 estaba cerrado
y el kernel devolvía ESRCH. Se conserva el registro de sus campos observados,
identificado como tal, sin fingir una copia byte a byte del state anterior.
Se creó el destino externo nuevo PID **36913**, ventana **19381**, con contador
base 0. La regla antigua no se heredó: la primera observación exigió aprobación.

Una sola operación nueva produjo **0→1**, con oráculo AppKit independiente,
postcondición exacta y recibo `known/verified`. El ACK nativo se conserva en un
campo distinto. No se repitió el press incierto del proceso que había salido.
El frame real nuevo tuvo **12.352 bytes**, SHA-256
`f7d30e601ff1dc2d9be96fd640baa693a8f11716ff9dcb5f340ca095a70b8cae`, una región
protegida retirada y entrega sólo por el canal efímero; SQL/log conservaron
metadatos, sin portador ni archivos de captura de control.

El coordinador eligió «Siempre para este destino» y confirmó «Permitir y
guardar» para la nueva regla `9f0101bb68204055b32b165c538f7825`. Cerró el SUT,
confirmó la retirada del socket y reabrió **los mismos bytes**, manteniendo vivo
el fixture externo. La regla persistió, pero la primera lectura dio
`target_changed` conocido, sin despacho: los handles nativos son referencias
efímeras en RAM. Dos lecturas explícitas aprobadas, resolve/windows, revalidaron
la misma aplicación y ventana físicas. La siguiente observación idéntica fue
verificada y usó `approval.remembered`, sin tarjeta ni decisión RPC del observador.
Es un prerrequisito de vinculación del destino; no se restauran handles como
si fueran permisos ni se cambió código para eludirlo.

El coordinador revocó la regla nueva por UI: revisión 8, permiso ausente. La
misma observación volvió a pedir aprobación y fue rechazada por CUA. El oráculo
SQL durable confirma `failed/approval_denied`, lista de recibos vacía y ausencia
de despacho nativo; el observador no envió decisiones RPC. El campo genérico
`outcome=unknown` de ese run fallido se conserva tal como está, sin reclasificarlo
ni presentarlo como una acción nativa ejecutada. Contador 1 y texto base quedaron
intactos. Después se retiró también la regla inactiva del destino anterior:
revisión 9, cero reglas persistentes o volátiles, cero acciones activas o sin
resolver, ninguna aprobación pendiente y cero runs persistentes no terminales.
El coordinador cerró la app de prueba y su socket de servicio y cerró el fixture
externo. El [estado final independiente](evidence/mac-browser-20261027/external-fixture-final-state.json)
registra `closed`, contador 1. No se cambió la instalación personal.

El [resumen final](evidence/mac-browser-20261027/rpc-qa04-closeout-summary.json),
[detalle inmutable](evidence/mac-browser-20261027/rpc-qa04-actions-permissions-closeout.json),
[oráculo SQL](evidence/mac-browser-20261027/rpc-qa04-rejection-sql-oracle.json) y
[añadido de limpieza](evidence/mac-browser-20261027/rpc-qa04-cleanup-no-permissions.json)
se conservan con sus hashes en el [manifiesto de evidencia](evidence/mac-browser-20261027/MANIFEST.json).
El resumen anterior al añadido conserva su estado histórico: la retirada final
de la regla inactiva no se inserta retroactivamente en aquel archivo.

El [código propio del fixture externo](evidence/mac-browser-20261027/JarvisMacExternalFixture.swift)
y su [manifiesto](evidence/mac-browser-20261027/FIXTURE_SOURCE_MANIFEST.json)
se conservan como evidencia documental reproducible. Su SHA-256 es
`568669cdd793d45cad1627f97c7c0f6297c713a75e7beb4d703c5c24c0e5d713`.
No es un módulo del producto ni una entrada adicional del manifiesto de 356.

La auditoría reprodujo que `stopLoading` no termina un JavaScript ya despachado.
Cancelar/timeout/reset conservan la espera de su callback real y el lane; no se
fabrica asentamiento. También se cerró el acceso a las ventanas de historial,
Ajustes y aprobaciones del propio host: sólo se admiten ventanas de navegador
propias identificadas y el fixture sintético elegido por una opción confiable.
Estos negativos y sus correcciones no son pruebas de TCC entre procesos.

Los fallos funcionales corregidos quedan separados de sus intentos iniciales:

- **Autoaprobación por control de la propia app:** historial, Ajustes y tarjetas
  quedan fuera de los destinos admitidos. Sólo se permiten ventanas propias
  identificadas del navegador y el fixture explícito de QA.
- **Limpieza WebKit ficticia:** detener carga o cerrar la ventana ya no resuelve
  una evaluación despachada. Se conserva hasta el callback real o terminación
  del proceso WebContent, con resultado incierto sin replay.
- **Carreras de lifecycle:** el host comparte una transición; stop conserva el
  ACK y gana sobre un arranque/reconnect pendiente. ChatStore bloquea enviar,
  cambiar chat y corregir turnos antes del primer await, conserva el runtime
  para reintentar un cierre incierto y sólo permite la selección inicial por
  una ruta interna guardada.
- **CAS invalidado por grupos AX vacíos:** se normalizan exclusivamente grupos
  de chrome inertes, manteniendo editor físico, contenido y destino. No se
  relaja el rechazo de un editor reemplazado o cambios semánticos.
- **Indicador falso de descarga:** sólo se afirma publicación después de
  comprobar el archivo. Fallos nativos o de publicación no conservan bytes
  ni un indicador de éxito.
- **ACK AX confundido con resultado:** se conserva `native_acknowledged` como
  metadato distinto de la postcondición pedida. Una lectura exacta posterior
  puede verificar ese estado con lease vigente aunque falte ACK; mismatch,
  lectura fallida o autorización retirada conservan incertidumbre, sin reenviar.
  No se cambió el timeout nativo.

En esta ejecución el dueño respondió expresamente **«puedes hacerlo tu»** para
los controles TCC de la app de prueba y completó Touch ID. Los toggles OS de AX
y grabación de pantalla quedaron activados. El intento 03 denegado se conserva;
con la identidad dedicada nueva se comprobaron AX y frame reales. El diagnóstico
del coordinador identificó comportamiento de permisos asociado a identidades
ad-hoc previas; no se presenta como solución universal de TCC ni se migran datos
del dueño. El runtime de desarrollo usa Python externo y copias privadas de
fuentes/dependencias en `/private/tmp`, sin ampliar la lectura a Documents.
Es una excepción puntual: las credenciales siguen reservadas al dueño y la
regla general de concesión TCC por el dueño no cambia.

La tarjeta de «Siempre», su reutilización entre chats, la nueva selección para
el destino reemplazado, persistencia tras reiniciar, revocación y rechazo
posterior ya pasaron. No quedan pruebas de acciones/permisos de este bloque
pendientes ni acciones en vuelo en el corte final. El cierre de recursos
sintéticos también quedó confirmado en el añadido final.
Los controles de paquete/smoke y hooks finales se ejecutarán al publicar; aún
no se marcan aprobados dentro de este snapshot. El commit que contenga este
registro se resolverá desde Git: no se incrusta un SHA futuro ni se autoatestigua
el propio commit. Los resultados efectivos de hooks, push a `main` y CI se
registran en sus metadatos y en la respuesta final del coordinador después de
observarlos, sin atribuirlos retroactivamente a este documento anterior al commit.

## Límites que permanecen

El peer SDK sólo propone una llamada ofrecida por mensaje explícito de prueba y
recoge su recibo. No es inferencia, no usa la cuenta del dueño y no cualifica la
comprensión de un modelo real ni todos los caminos del cliente oficial.

Un callback JavaScript despachado puede seguir corriendo tras cancelación; un
sitio o una página CDP externa puede mantener actividad autónoma. Detener carga
o cerrar el transporte no demuestra que ese código dejó de actuar ni revierte
su efecto. Se retiene la custodia cuando la terminación es desconocida, sin
autorizar el siguiente efecto o repetir el anterior. Tampoco se acredita cada
sitio, iframe de otro origen, popup, extensión, autenticación web, semántica
`isTrusted` o entrada física universal.

La PTY no permite fork ni árboles de procesos arbitrarios. Las comprobaciones
AX/WindowServer no son atómicas ni aislamiento frente a malware del mismo
usuario. Redactar controles protegidos no detecta cualquier secreto dibujado
sin semántica AX. No se lee historial personal; la excepción TCC descrita arriba
no delega contraseñas ni autorizaciones de otros perfiles.

Se conserva el canal `development` con Python y Codex externos. Faltan
distribución autocontenida, Developer ID/notarización y actualización remota
firmada; este incremento no reinstala ni reemplaza la app anterior del dueño.
macOS Apple Silicon sigue como destino inicial, iPhone sigue aplazado,
Codex con ChatGPT continúa como elección de inferencia y no hay NVIDIA ni
fallback oculto.
