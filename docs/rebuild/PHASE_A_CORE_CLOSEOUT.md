# Cierre del bloque de núcleo y sesiones

El contrato finito solicitado queda implementado y cualificado: identidad local
firmada, recuperación del servicio, compactación y ramas, coordinación de hijos,
presupuestos, cola y corrección de turno. La app SwiftUI ofrece controles propios
para cada recorrido. **Este cierre no termina toda la fase A** ni declara revisada
toda la referencia OpenClaw. La fase B continúa aplazada.

El [registro verificable](PHASE_A_CORE_CLOSEOUT.json) conserva las 156 fuentes
productivas y sus hashes, resultados SQL, oráculos independientes, IDs de las
nueve decisiones nativas y hashes de logs/artefactos. Los contratos detallados son
[servicio e identidad](PHASE_A_CORE_SERVICE.md),
[contexto y ramas](PHASE_A_CORE_SESSIONS.md) y
[coordinación y cola](PHASE_A_CORE_DELEGATION.md).

## Investigación REA por partes

OC-21A examina identidad y recuperación de locks; OC-21B, compactación y ramas;
OC-21C, alcance de hijos, recuperación y steering. Cada resumen conserva sus
fuentes, hashes, blobs Git, Evidence IDs e incógnitas. La referencia es OpenClaw
`1e3ff306abaff29f03e7668d53b01c07f10294ac`.

Los intentos iniciales sobre el árbol de consulta detectaron entradas cambiantes
de iCloud. Se rechazaron esas identidades; no se continuó relajando la integridad.
Las particiones posteriores se obtuvieron de los blobs exactos con `git show`,
en carpetas privadas inmutables. No se modificó ni ejecutó el clon y no se copió
su implementación al producto. El registro acumulado contiene **23 lotes y 90
fuentes TS/JS únicas**, con semántica y dependencias todavía pendientes. Inventario,
análisis estático y cualificación del producto propio son evidencias diferentes.

## Comportamiento implementado

| Contrato | Resultado y frontera |
| --- | --- |
| Identidad local | Ed25519 con CryptoKit/`cryptography`, perfil físico, servidor, nonce, secuencia y cuerpo exactos; ámbitos `read`/`owner`, admisión CAS y revocación durable. El bearer sólo descubre la conexión. No es atestación Apple. |
| Recuperación | Salida del hijo propio y perfil/lease comprobados, socket muerto recuperable, custodia de hijos cooperativos y límite de recuperaciones. Se retiran aprobaciones y cuentas de la vista y sólo se recarga estado local. No se reenvían runs ni se arranca el SDK por la recuperación automática. |
| Contexto | Compactación extractiva acotada que conserva decisiones literales y recibos históricos, con omisiones declaradas y originales intactos. La cápsula pasa como datos históricos; no concede permisos. |
| Ramas | Fork completo y rewind conservador con revisión CAS e identidad durable. Crean otra conversación sin deshacer efectos, heredar permisos ni reutilizar el hilo del proveedor. Consulta después de ACK perdido sin repetir la mutación. |
| Delegación | Hasta cuatro hijos de un nivel, sesiones nuevas, agente/revisión exactos y aprobaciones de herramientas independientes. Presupuestos y plazos finitos; el padre espera limpieza y persistencia de los hijos. |
| Cola/corrección | Hasta cuatro sucesores; esperan el recibo y limpieza anteriores y se bloquean ante incertidumbre. `turn/steer` liga la corrección al turno exacto y no reintenta un ACK perdido. |

Las firmas usan una dependencia productiva fijada y auditada por el guard del
paquete. El arranque conserva `-I -S`, rutas explícitas y ausencia de ejecución
de `.pth`/`sitecustomize`. El smoke instala desde lock con hashes y sin red y
comprueba una firma real. La dependencia no convierte el bundle en autocontenido.

## Recorrido de la app de prueba

Se compiló un bundle propio y se cambió únicamente su identidad/nombre en
Info.plist, con nueva firma ad hoc, para no resolver la app ni las preferencias
personales. La fuente Python se congeló en una copia privada. El agregado de las
156 fuentes es `c750cd9001830ba7ff2de063ea06ab717bbafc5106a61f674f7accf70ce49083`.
El registro distingue el ejecutable canónico del ejecutable vuelto a firmar.

Todo ocurrió en `/private/tmp/jarvis-core-sessions-native-20261026-03`. El dueño
delegó las aprobaciones de ese perfil sintético. El agente pulsó los botones de
la interfaz; las nueve decisiones no fueron inyectadas directamente por RPC.
El transporte firmado, SQLite, Runtime y escritura de archivos fueron reales.
**El SDK de modelo fue un peer stdio propio y sintético**, sin red ni acceso a
la cuenta del dueño. No se presenta como una nueva inferencia ChatGPT autenticada.

| Recorrido nativo | Comprobación independiente |
| --- | --- |
| Compactar | Origen de nueve mensajes conservado; decisión literal `PRUEBA DECISION CORE TOPACIO 5192`; cinco recibos históricos. Cápsula de 3438 bytes y siguiente inyección al SDK comprobadas. Los ocho mensajes elegibles conservados no incluyen solicitudes de runs fallidos. |
| Fork y rewind | IDs nuevos, linaje exacto, decisión y recibos conservados como datos; origen intacto, sin heredar permisos ni revertir efectos. El rewind cortó en el mensaje 4 de la rama. |
| Objetivo con dos hijos | Jarvis revisión 2 y analista revisión 1, 20 pasos reservados y 120 segundos. Aprobación de la delegación separada de la escritura hija. Padre e hijos terminaron con recibos SQL. |
| Escritura hija | Un único efecto autorizado: 20 bytes `PRUEBA NUCLEO HIJO 2`, SHA-256 `644aa9fc8641a3cf97e70a77cd07aa4ee84b027f27938ab2bddfa322b290ae3c`. El reporte del modelo siguió separado del recibo y del oráculo de archivo. |
| Cliente de lectura | Admisión nativa, lectura firmada permitida y mutación/admin/inicio de SDK rechazados. Revocación nativa conservada; el mismo cliente dejó de leer y el propietario siguió operando. |
| Cola y steering | Sucesor SQL en `queued` sin turno enviado mientras el anterior esperaba. Una corrección aceptada para su turno exacto; después, sucesor despachado una sola vez. Ambos recibos terminales conocidos. |
| Caída con Mantenimiento abierto | Se mató exclusivamente el servicio sintético propio mientras otra escritura esperaba aprobación. El supervisor recuperó una nueva identidad aun con los lectores de Ajustes pausados. El run quedó `interrupted/unknown`, sin tarjeta antigua ni repetición de la escritura. El SDK mantuvo una sola apertura. |
| Cierre y reapertura | Cierre normal y reapertura explícita del mismo perfil. Catálogo de compactación/fork, rama rewind, objetivo completado, recibo hijo y cliente revocado visibles. Ningún turno, propuesta, respuesta de herramienta o steering adicional. La segunda apertura del SDK corresponde al arranque explícito, no a la recuperación automática. Cierre final sin socket ni procesos propios del peer. |

## Controles y correcciones acreditados

Las suites integradas finalizaron con **2536 pruebas Python aprobadas** y
**332 Swift, cero fallos**. Ruff, compilación, guard de base limpia, wheel desde
sdist fresco, instalación aislada offline, build y verificación del bundle
pasaron. Los hooks vuelven a comprobar el árbol congelado al hacer commit/push;
la CI pertenece al commit resultante y se consulta por su SHA.

Se conservan los fallos iniciales. El primer control Python pasó 2532 casos y
falló en tres: una clave sintética creada dentro del corpus de un inventario de
investigación y dos expectativas obsoletas de 13 herramientas, ahora 15. Se movió
la clave fuera del corpus y se exigió el conjunto exacto de herramientas, sin
relajar las garantías de privacidad o ausencia de llamadas. El primer control
Swift tuvo dos fallos del mismo fixture histórico de caída: todavía exigía
rechazar y borrar a mano el socket muerto que ahora recupera el producto. Se
actualizó ese oráculo, conservando las negativas de socket vivo, lease ocupado
y perfil cambiado. Los controles completos posteriores pasaron.

La auditoría independiente también reprodujo y corrigió fallos de producto:
supervisión pausada por Mantenimiento, una lectura que podía sobrescribir la
selección de objetivo, revisiones antiguas de agentes, tarjetas anteriores a la
caída y pérdida de decisiones/recibos en la ruta antigua de fork. Las regresiones
forman parte de las suites finales; no se suman los focales como pruebas distintas.
Una segunda revisión de solo lectura no encontró bloqueantes nuevos.

### Corrección de la CI con caché nueva

La primera publicación `9a6c5be` aprobó los gates locales. Su CI
[`40441066936`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40441066936)
aprobó los contratos Python en ambos sistemas y las 332 pruebas Swift y el bundle
en macOS, pero rechazó el smoke offline del paquete en ambos runners. El fallo
queda conservado; no se presenta esa CI como aprobada.

La reproducción con una caché inicialmente vacía confirmó la causa: `uv sync
--locked` había descargado los artefactos por sus URLs del lock, sin metadatos
del índice simple. El posterior `pip install --offline --require-hashes` sobre
requisitos exportados intentaba resolver por nombre y no encontraba `cffi`,
aunque su artefacto estaba en la caché. Una caché local usada con anterioridad
había ocultado esa dependencia accidental del índice.

El guard ahora sincroniza directamente el mismo `uv.lock` con `--offline`,
`--locked`, `--no-build` y sin instalar el proyecto. Fija exclusivamente el venv
privado del smoke mediante `UV_PROJECT_ENVIRONMENT`; no cambia el entorno del
repositorio. Después instala el wheel propio sin índice ni dependencias y prueba
su importación y la firma Ed25519. Un fallo conserva un diagnóstico acotado.
No se elimina el modo offline ni la identidad de hashes del lock.

Cuatro regresiones nuevas usan un servidor HTTP local que sólo sirve un wheel
sintético por URL. Preparan una caché vacía sin consultar índices, reproducen el
fallo anterior y comprueban la instalación nueva sin otra solicitud HTTP. También
rechazan hash discordante, caché ausente y lock obsoleto, conservando el proyecto.
El total integrado pasa a **2540 pruebas Python**; Swift mantiene 332. Los hooks
repiten ambos conjuntos sobre la revisión corregida antes del push. Las 156
fuentes productivas y los recorridos GUI anteriores conservan sus hashes: esta
corrección sólo cambia el guard de empaquetado, sus pruebas y la documentación.

## Lo que este cierre no acredita

La clave local no protege contra un programa malicioso con el mismo usuario;
la custodia por descriptor cubre hijos cooperativos controlados. La compactación
declara omisiones y presupuestos. La coordinación tiene un nivel y cuatro hijos,
sin afirmar multitud distribuida ni todos los harnesses. No se acredita acceso
remoto, agrupación completa de sesiones ni calidad de inferencia real con el peer.

La distribución sigue siendo `development` con Python y Codex externos: faltan
autenticidad de distribución, paquete autocontenido, Developer ID/notarización
y actualización remota firmada. No se reinstaló ni leyó la app personal, sus
perfiles, `auth.json` o historial. La matriz conserva **44 familias parciales y
36 sin implementación, ninguna familia amplia cerrada**. Los demás bloques de
A permanecen explícitos; B no se inicia por cerrar este contrato.
