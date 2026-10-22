# Quinta revisión: conexiones, ciclos y fronteras — 22 de octubre de 2026

Continúa [la cuarta revisión](CODEX_REA_FEATURES.md). El artefacto sigue siendo
Codex `26.1016.51333`, instalado en `/Applications/ChatGPT.app`, con ASAR SHA-256
`9d0260dd37f9c45546d5a89d9d782b8362c9957b70b992e2c3f91df60a046b07`.
Se tomaron criterios de diseño y se implementaron correcciones propias en Jarvis.
No se incorporó código ni recursos del artefacto al repositorio, ni se consultó
el historial, credenciales, permisos o tráfico personal del dueño.

## Alcance por frente

| Frente solicitado | Evidencia añadida | Resultado propio en Jarvis |
| --- | --- | --- |
| Módulos centrales y conexiones | 53 declaraciones completas seleccionadas de `main-JI9qDiXp.js`, `app-initial-f3e281fb9a71.js` y `app-primary-9bb8fd8d770a.js`. | Recibos de borrado validados, exclusión de operaciones no admitidas y descarte de respuestas antiguas en Ajustes. |
| Dependencias complementarias | 21 módulos completos de Git/PR, documentos, marketplace, interfaz y metadatos de apps. | Registros de complementos conservados ante corrupción; publicación coherente de marketplaces y fallos de instalación comunicados como fallos. |
| Comportamiento dinámico | Escenarios controlados sobre el runtime real de Jarvis con relojes, callbacks, almacenamiento y transportes sintéticos. | Permisos permanentes sin escrituras tardías después de cancelar/revocar; tareas desatendidas canceladas y drenadas si falla su seguimiento. |
| Componentes nativos y servicios remotos | Tres Mach-O arm64, firmas, fuses y contratos del cliente de Codex; transportes IPC y NVIDIA simulados en Jarvis. | Plazo acumulado de IPC, cierre seguro del peer y consistencia de identidad en respuestas NVIDIA en flujo. |

Los detalles y límites de los otros bloques están en
[dependencias](CODEX_REA_DEPENDENCIES_NEXT.md),
[escenarios dinámicos](JARVIS_DYNAMIC_REVIEW.md) y
[fronteras nativas/remotas](CODEX_REA_NATIVE_REMOTE.md).

## Módulos centrales: evidencia y procedencia

Un índice AST de los tres archivos originales registra 35.954 declaraciones de
nivel superior e importaciones literales en 21.170.648 bytes. Es un índice para
seleccionar y conectar ubicaciones, no una revisión exhaustiva de esos archivos.
De él se seleccionaron **53 declaraciones**, con **265.981 bytes** exactos, sin
envoltorios ni transformaciones. REA 6.7.0 las analizó por separado con 768 MiB y
45 segundos por unidad, hasta 40.000 nodos semánticos y 20.000 incógnitas.

Las 53 unidades completaron el análisis sin errores sintácticos, fuentes omitidas
ni truncamiento por recursos: 72.195 nodos AST, 44.007 nodos semánticos y 42.974
relaciones retenidas. Las 16.091 incógnitas estáticas no son defectos; las llamadas
indirectas y los valores que cruzan declaraciones siguen sin quedar resueltos.
Una repetición del indexador auxiliar con 1 GiB agotó memoria; se conserva el
índice anterior completo. Independientemente se verificaron los hashes de las
tres fuentes, cada rango seleccionado y los 53 archivos completos de evidencia.
El fallo auxiliar no se cuenta como un análisis REA completo ni como un defecto.

El [manifiesto](CODEX_REA_CORE_CONNECTIONS_COVERAGE.json) conserva los rangos
UTF-8/UTF-16, hashes, importaciones, estadísticas, IDs de evidencia y limitaciones.
Fuentes, scripts y evidencia completa permanecen en
`/private/tmp/codex-rea-core-next-20261022`. Los archivos temporales no constituyen
un archivo permanente ni se publican como parte de Jarvis.

## Contratos observados

| Ubicación original | Hecho observado en texto inerte | Criterio de contraste |
| --- | --- | --- |
| `main`, declaración 165, bytes `[47639,51205)` | Solicitudes pendientes indexadas por ID; retirada antes de resolver; rechazo y limpieza al cerrar; comprobación de cierre después de esperar un manejador. | Una respuesta tardía no debe resolver otra operación ni reactivar una cancelada. |
| `main`, declaración 208, bytes `[60365,60986)` | Inicio diferido con bandera de ejecución, señal de cancelación y retirada de temporizador. | Cancelar debe invalidar tanto la espera como sus continuaciones. |
| `main`, declaración 678, bytes `[272180,272357)` | El acuse de mensajes comprueba el emisor IPC y valida el payload antes de consumirlo. | Identidad de transporte y recibo válido son condiciones distintas. |
| `main`, declaración 3902, bytes `[4021884,4031704)` | Cancelación de RPC vinculada al worker y al ID de solicitud, con AbortController. | La cancelación conserva la identidad del trabajo admitido. |
| `app-initial`, declaración 1780, bytes `[1645472,1646625)` | Mutación de configuración con estado anterior, recuperación de error e invalidación de consultas al terminar. | El estado visible debe converger con el resultado confirmado de la operación. |
| `app-initial`, declaración 4377, bytes `[3115042,3115882)` | Solicitudes de herramientas dinámicas con ID y plazo de diez segundos; temporizador y registro retirados antes de resolver. | Las esperas tienen límite y una finalización consume su registro una vez. |
| `app-initial`, declaraciones 5246/5252 | Transiciones de instalación condicionadas por host e ID de solicitud. | Una respuesta anterior no debe reemplazar una revisión o instalación posterior. |
| `app-primary`, declaración 2819, bytes `[1706810,1707126)` | Distinción entre rechazo, resultado desconocido, aceptación y resultado parcial. | Una operación incierta o rechazada no debe presentarse como completada. |

Las importaciones enlazan ubicaciones literales del cliente; no prueban ejecución
ni autorización del servidor. Las conexiones dinámicas conservan ese límite.

## Correcciones en Ajustes

Antes, los métodos de borrado comprobaban una bandera global de error después de
llamar a `perform`. Si la app estaba ocupada, `perform` retornaba sin enviar la
solicitud, pero la fila desaparecía igualmente. Ahora el resultado pertenece a
la operación concreta; una solicitud no admitida no retira recuerdos, permisos,
tareas ni objetivos del estado visible.

Los recibos de revocación y borrado exigen un booleano del daemon. `false` acredita
ausencia previa según sus contratos y se acepta de forma explícita. El borrado de
memoria exige además el identificador solicitado y el estado `deleted`. Un
payload vacío, malformado o de otro recuerdo no acredita el borrado. Un error de
refresco posterior no anula un borrado que sí fue confirmado.

La identidad de operación invalida respuestas de monitores y listados anteriores.
Se comprueba después de esperar y antes de cambiar el estado, mostrar éxito o
abrir una URL de autorización. Cancelar también impide que una respuesta tardía
de inicio cree otro monitor. Las lecturas de configuración y extensiones que se
solapan con una mutación se descartan y dejan un nuevo refresco en cola, para
converger con el estado vigente.

## Verificación y límites

Las pruebas de Ajustes inyectan transporte y datos sintéticos; cubren exclusión
por ocupación, recibos malformados, identidad de memoria, fallo posterior de
refresco, listados tardíos, cancelación antes del inicio y monitores cancelados.
Incluyen convergencia de configuración y extensiones después de lecturas viejas.
Las otras regresiones y sus fallos anteriores al arreglo están documentados en
los informes enlazados. La calificación final del lote usa ruff, pytest, suite
Swift completa y los gates de commit/push con paquete release, aceptación,
producción y fiabilidad.

Antes del commit aprobaron **4.633 pruebas Python** (46 omitidas) y **557 pruebas
Swift** (168 XCTest y 389 Swift Testing). Ruff y el catálogo español pasan: 1.270
claves, incluidas las 1.264 extraídas por el compilador. Los hooks repiten sus
controles requeridos sobre el árbol final antes de publicar.

**No se declara una revisión exhaustiva de Codex.** Quedan declaraciones y
dependencias no seleccionadas, resolución dinámica entre módulos, implementación
interna de otros binarios nativos y comportamiento de sus servicios privados.
Los escenarios dinámicos ejecutan Jarvis, no Codex. Las pruebas de transporte
NVIDIA no consultan una cuenta ni demuestran el comportamiento del backend de
Codex. Tampoco se cierran las cuatro certificaciones/distribución pendientes de
V1 ni las ampliaciones futuras P3.
