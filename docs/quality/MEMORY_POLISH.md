# Pulido — Memoria

Base: `214930a`. Fecha: 2026-09-11. La memoria sigue siendo local; este bloque no cambia el
CLI `nvidia_only`, habilita otro proveedor ni concede autoridad a los recuerdos.

## Correcciones verificables

Las 11 regresiones iniciales reprodujeron títulos y turnos en texto plano, metadatos alterables,
fechas comparadas como texto, recuperación de recuerdos vencidos, bloqueos acumulados y escrituras
que sobrevivían a la cancelación de su tarea. La cobertura nueva amplía estos casos con migraciones,
procedencia de propiedades físicas, límites del grafo y olvido concurrente.

| Área | Contrato actual |
| --- | --- |
| Conversaciones | AES-GCM para títulos y contenido, con dominio separado de los recuerdos. Se autentican namespace, identidad, rol, secuencia, fechas, hash y cantidad de turnos. |
| Migración v8 → v9 | Clave comprobada antes de modificar el esquema; filas procesadas por cursor en una sola transacción. Conserva IDs, títulos, turnos y fechas; un error revierte el paso completo. |
| Integridad | Una alteración de historial bloquea el subsistema. La lectura comprueba la secuencia del sufijo y el append contrasta el total autenticado; se mantiene el borrado en cascada. |
| Caducidad | Comparación de instantes con zona horaria y precisión de microsegundos, común a etiquetas, FTS5, RRF y expulsión. No depende del mantenimiento térmico. |
| Grafo | Documentos y aristas vencidos no se recuperan. Una entidad compartida necesita procedencia vigente; IP/MAC/token se reconstruyen desde recuerdos autenticados aún válidos. |
| Vectores y Spotlight | Candidatos y exportación filtran caducidad. La selección vectorial considera el índice acotado completo antes del límite de resultados, evitando que vecinos vencidos oculten los válidos. |
| Concurrencia | Las lecturas compuestas usan una instantánea SQLite. Los límites de recorrido no devuelven aristas sin ambos extremos. |
| Cancelación y olvido | Se espera al worker SQLite antes de liberar una conversación. Los bloqueos cuentan también esperadores y se retiran al quedar vacíos. La caché GraphRAG usa LRU de 256 sesiones y descarta búsquedas que cruzan un reset. |

Las propiedades de un dispositivo compartido no se consideran vigentes solo porque el dispositivo
conserve otra relación. Su proyección puede recuperar el valor anterior todavía válido si una
observación posterior ya caducó o fue borrada. El digest del nodo almacenado sigue siendo el token
de concurrencia del índice; la proyección no reescribe ni elimina recuerdos durante una consulta.

## Límites explícitos

Se reutiliza el [contrato AES-GCM de cryptography](https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM):
nonces aleatorios independientes y AAD ligada a la identidad. Los IDs, namespaces, fechas,
secuencias, cantidades y hashes de contenido siguen siendo metadatos consultables; esto no es
cifrado de todo el archivo SQLite. No se promete detectar el reemplazo completo de la base por una
copia antigua auténtica. La migración no puede acreditar retrospectivamente la autenticidad de
un historial que antes solo tenía un hash sin clave.

`secure_delete=ON` limpia las páginas lógicas reemplazadas de las tablas ordinarias según el
[contrato de SQLite](https://www.sqlite.org/pragma.html#pragma_secure_delete). No borra copias de
seguridad, snapshots APFS ni garantiza borrado físico de bloques SSD. No se eliminan esos archivos
externos. Un lector antiguo que retenga una instantánea puede conservar su vista previa.

Cancelar no revierte una transacción que ya se haya confirmado: garantiza que no queda un worker
abandonado tras devolver la cancelación. El tiempo de cancelación incluye ese drenaje y el timeout
SQLite de cinco segundos para bloqueos. Un reset concurrente descarta conservadoramente resultados
PPR en curso, incluso si otro namespace provocó el cambio de época; no borra sus datos o cachés.

La exportación nueva de Spotlight excluye datos vencidos; este contrato no implica que macOS borre
instantáneamente una copia exportada anteriormente. El índice nativo es opt-in y se reconstruye
en la siguiente sincronización. El código no activa esa opción.

## Verificación reproducible

Las pruebas usan exclusivamente bases temporales y datos sintéticos; no leen el historial personal
ni imprimen claves. Incluyen migración interrumpida y recuperación, claves incorrectas desde v4/v7/v8,
filas huérfanas, hashes y fechas corruptos, trasplantes entre conversaciones/namespaces, truncación,
cancelación de esperadores y escrituras, caducidad en UTC−12/UTC+14 y 40 vectores vecinos vencidos.

```bash
.venv/bin/python -m pytest -o addopts='' -q \
  tests/test_memory_privacy.py tests/test_conversation_migration.py
.venv/bin/ruff check src tests
.venv/bin/python -m pytest -o addopts='' -q
```

La suite completa requiere sockets Unix locales. Los hooks obligatorios añaden las pruebas Swift,
el paquete macOS firmado, aceptación, flujos de producción y fiabilidad prolongada. La instalación
se valida con `./script/jarvis_beta.sh check`; las revisiones de Git y del bundle deben coincidir.

Resultado de esta revisión: **37 pruebas nuevas de memoria** y **1498 pruebas Python correctas,
17 opt-in omitidas** en la suite completa (31,45 segundos en esta ejecución). Ruff y
`git diff --check` también pasan. Estas cifras son evidencia de la revisión, no una garantía de
ausencia de defectos ni un presupuesto de latencia para otras máquinas.

No hay downgrade automático de v9 a v8: un binario antiguo debe rechazar el esquema nuevo. Ante una
migración inválida se conserva el historial original y se informa del fallo; no se sustituye la base
por una vacía ni se genera otra clave silenciosamente.
