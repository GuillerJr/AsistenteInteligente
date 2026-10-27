# OC-23: memoria, recuperación automática e indexación

Investigación estática parcial del 27-10-2026. Las propuestas de este documento
son decisiones propias para Jarvis; no acreditan implementación ni ejecución de
OpenClaw. No se ejecutó su código ni se importaron sus fuentes al producto.
En el corte de esta investigación faltaba elegir el motor de embeddings. Después,
el dueño eligió expresamente MiniLM multilingüe local en CPU el 27-10-2026.
La investigación no descargó modelos, instaló dependencias ni ejecutó suites locales.

## Identidad y adquisición

Referencia de sólo lectura:
`/Users/guillerjr/Documents/Proyectos/Jarvis-Base-OpenClaw`, revisión fija
`1e3ff306abaff29f03e7668d53b01c07f10294ac`.
Se inventariaron rutas con `git ls-tree` y se adquirieron blobs exactos mediante
`git show revision:ruta`, sin cambiar el clon. Cuatro particiones privadas con
fuentes de modo 0400 y directorios 0500 permanecen en
`/private/tmp/jarvis-rea-oc23-memory-rbu8pjpw/`.

El manifiesto de adquisición `acquisition.json` tiene SHA-256
`306efe6135fe6e5a4f09b2cecbb6f6a713d3117114f12c3f15765ecbdee58970`.
Se verificaron de nuevo todos sus hashes después de analizar. Son 13 rutas TS
únicas, 123.007 bytes de código fuente, más tres manifiestos distintos. Los 21
archivos de las cuatro particiones incluyen manifiestos repetidos; no son 21
fuentes distintas.

Se leyó la habilidad `reverse-engineer-anything` y su referencia
`javascript-applications.md`. Cada partición recibió una llamada real
`analyze_javascript_application`, `detail=summary`, `integrity_policy=fail`, selección
explícita de todas sus rutas, heap solicitado de 512 MiB y plazo de 60 segundos
por fuente. Límites semánticos solicitados: 30.000 nodos/18.000 incógnitas en total
y 10.000/6.000 por archivo. No se relajó integridad ni se repitió análisis para
ampliar una salida. El proveedor devuelve `analysis_profile=null`; no se midió de
forma independiente el consumo del proceso ni se afirma una cota física de RSS.

| Partición | TS parseados | Bytes de texto incluidos, con manifiestos | Incógnitas semánticas | Grafo de aplicación / semántico |
|---|---:|---:|---:|---|
| OC23A-search | 3 | 204265 | 1156 | complete / unknown |
| OC23B-autoindex | 3 | 168365 | 361 | partial / unknown |
| OC23C-recall | 4 | 206451 | 1049 | partial / unknown |
| OC23D-maintenance | 3 | 171424 | 341 | complete / unknown |

Las 13 fuentes parsearon sin errores ni archivos omitidos. Eso no resuelve las
2.907 incógnitas semánticas ni analiza las dependencias fuera de las particiones.
`complete` se refiere al grafo de esa entrada pequeña, no al repositorio ni al
comportamiento en ejecución. B y C conservan cobertura de aplicación parcial.

## Fuentes exactas

| Ruta original dentro de la revisión | Bytes | Blob Git | SHA-256 |
|---|---:|---|---|
| `package.json` | 153025 | `cc10d5f094978391dd1e0fe89e5054eb5c664b3d` | `7a5dc63cff6b85c63ba307ccd055ed4a5a105a4e223e27ec61fa6e24428618c5` |
| `extensions/memory-core/package.json` | 1600 | `e2d2e826c228b9de2982e5b7a78596f374e61ac7` | `3bce8cac3582d74800871a31582faa3b3ae0252a8c938bf40849308241c9466b` |
| `extensions/memory-core/src/memory/hybrid.ts` | 11368 | `a1a9ec1787933cd2eb8a10e5c380afe66edc32bf` | `b22b8c9fe3a88e0da9ebc3c2f16e9b0db61a9df618785c5091560dd3b7bceef6` |
| `extensions/memory-core/src/memory/manager-search-orchestration.ts` | 27384 | `0b3ff669ac46a9ed3e28d992b36f6984ad733627` | `07ba2fa8a09f5b0649ad50c320f73525bdb2edf216b0ba5b701744913f81237c` |
| `extensions/memory-core/src/memory/manager-embedding-policy.ts` | 10888 | `310d81957d096a8b4ed6a5e1b164786db12e68d3` | `6ce6c1cbf17b8e655d3dc381d0230a5c23a8ddd44f668f294a190ffd10feb007` |
| `extensions/memory-core/src/memory/manager-watch-ops.ts` | 5360 | `fe184e3ee613191142b0735196dbd7cc250ec4be` | `391240211b8247018158170d0cca450b25b41ed46a0b95ded1a80dce88bb9944` |
| `extensions/memory-core/src/memory/manager-sync-control.ts` | 5230 | `0e79e9833bdbcf93c84561ed7cd9bc1320ede400` | `24b9bf580b01470f292f08d571f8cf4b9ed5f807bf4b734e809d21a695324305` |
| `extensions/memory-core/src/memory/manager-index-generation-lease.ts` | 3150 | `49844fc63668f63d5454214976cd0a3f8d690e02` | `e2b1c0649b0c56c3950887cc14a154d9678be72e61df3bf76f1d8d281d75b158` |
| `extensions/active-memory/openclaw.plugin.json` | 10598 | `159c39e01b0fd1f4ebee35e5ec7b3edd9c3946ac` | `0b1672f3cd8dd4d16049fad6eff551224a43d2972a21bc888bd4589490216acd` |
| `extensions/active-memory/recall-run.ts` | 14673 | `054fbded93a410157a6f38710725c82c5267392b` | `7fcd958b6ab14eae2813914fcf378b46619d159126fea85d3afad34dfd1f9fa5` |
| `extensions/active-memory/recall-state.ts` | 7841 | `fbf2fff27135fa308a70bfc8c2baa55b09c1ea39` | `57c3d2565a254659ae5b06847ee84983a1e4fb0faff12db92da1bfe4eaa763be` |
| `extensions/active-memory/session-policy.ts` | 10484 | `81ad71ee74a0ef3af8052483a52a3f60d174add6` | `28242048b0b703ebc4b5ee38d6eddd3c92ac99d359828bbd1747fd632d4b66f6` |
| `extensions/active-memory/config.ts` | 9830 | `1cace3446ef27f2d63b843fc121fcf684a788dc3` | `461941ef4d6c3721d8e55b95d1f4d922417a9bb35346acbd37a2b1e5b1730327` |
| `extensions/memory-core/src/memory/manager-search-maintenance.ts` | 3211 | `8f63256cc2539dce63c36526c60023a3d51f4e0b` | `8aa8543b6c884b016e74bc1d6a8f71d1f9129c4ee50573c27971a527aa531fa6` |
| `extensions/memory-core/src/memory/manager-reindex-ops.ts` | 11660 | `701ae705a27e95189fbe73a53bc5feeaa679c7d9` | `64064e6df32c0c532d233b9849ef40a167495b86093a781216bd29e93a8daf18` |
| `extensions/memory-core/src/memory/manager-reindex-lock.ts` | 1928 | `03c5774b420f0187acb5c886f99f0b7be2dd0631` | `bf6aa745b3c2a5fed1043062f66e96c79630637da408f42665c5aa4d13370515` |

A contiene híbrido, orquestación de búsqueda y política de llamadas de embeddings.
B contiene watcher, cola de sincronización y leases de generaciones. C contiene
recall, estado/caché, política de sesiones y configuración. D contiene mantenimiento
de búsqueda, reconstrucción y lock de reconstrucción. Cada partición incluye
`package.json`; A/B/D incluyen el manifiesto de memory-core y C el de active-memory.

## Evidencia REA conservada

| Partición | Análisis completo retenido | Resumen | Traza focal de módulo, dirección outgoing |
|---|---|---|---|
| OC23A-search | `ev_b530db2ea6e25d3a7d7978517e5908806a2edfcf34c3704ecfde2c577837d17e` | `ev_66482b15d81c61bee743cd1779b9440b12c5f9911ad1f0be34be4480b923c21f` | `ev_9c90a57a9732c90269f08e1288bfe6bde047de3508af62d7c064fc4bb67981ca` |
| OC23B-autoindex | `ev_b3ed8cd797c9756d358ddcbe7fa4b441e6d159b0dd4b0a63b43a2661a762c92e` | `ev_680f84b7b42f8d5fab1a8d8070731759817358d0d25da37c25d74588dde8e817` | `ev_6641884c1a16c3f9faa3cd7a77f95b330538ced5005805b2cde765371234a7b1` |
| OC23C-recall | `ev_9bcfd8202741ec92f7b58e4d3bcecbe9fd07809480934bdd0d1c764fc72314f9` | `ev_b6454145ed0e8daafb6267fda02a7fb5fc557724ec4971958c3c12c48e1923d8` | `ev_b9d854af7c416d3244a96259733d33ff198e7de744bface6f4982b0c45575eff` |
| OC23D-maintenance | `ev_0d8e3689321c4a12717190ecc51e004f1e4fc6cf9f614969925f5d3879a9f810` | `ev_01d742b6a068bc9aebf793519f1f3be7a9da5b360c0e7344f429957cbd11fb10` | `ev_add94b76f717a13b70ef82f57c26db305d59aa40b6493ca66b03a9eef0626c6a` |

La página de identidades A tiene Evidence ID
`ev_4d9cb88787036aff1da63272357c30126637e3f12c1a233a7c41dc721f694b66`.
El item exacto de `hybrid.ts`, seleccionado por su node_id, tiene
`ev_dff235f0b2e52a5970f97e44a8b82433605019a5eaf2e1b5c441fb1145d2e00e`.
Primero se trazó la API literal `mergeHybridResults`: el resultado
`ev_f7722cc2cdc05c91ec2b7f6539208fdac8318535dbe822941139b10afc89342f`
fue `no-match`. Se conserva ese resultado: no prueba ausencia del export ni del
comportamiento. La pregunta se reformuló con el módulo exacto, sin reanalizar la
fuente, y se obtuvo la traza A indicada arriba.

Las trazas A/B/C/D alcanzaron respectivamente 11/6/79/11 nodos y 12/5/120/10
aristas. A y D declaran `complete-within-source`; B y C siguen parciales. Sus
relaciones estáticas/inferidas no prueban ejecución ni que todos esos nodos
participen en la función bajo cualquier configuración.

Bundle completo exportado mediante `export_evidence_bundle`:
`/private/tmp/jarvis-rea-oc23-memory-rbu8pjpw/evidence.json`, 17.027.821 bytes,
15 registros, SHA-256
`b265b41078e5a4d614970f2634301dcb09378faafc6cf143aace52f40bc3ad03`.
Los cero `unknowns` de su ledger de incógnitas registradas son distintos de las
2.907 incógnitas de los grafos semánticos, que siguen abiertas. No hubo sesiones
nativas ni `open_binary`; no se cerró estado de otro investigador. El bundle está
en almacenamiento temporal: el hash documenta su identidad, no garantiza su
conservación indefinida. Este informe conserva la selección y metadatos esenciales.

## Hallazgos estáticos y límites

### Búsqueda semántica e híbrida: parcialmente respondida

En A, `hybrid.ts` reúne resultados por identidad, conserva señales léxicas y
vectoriales y la procedencia, y distingue elegibilidad del orden final. El código
pondera contenido y texto, trata la coincidencia exacta de ruta de forma específica
y llama a diversidad MMR y decaimiento temporal. Esos dos algoritmos son imports
no incluidos; aquí no se afirma cómo calculan sus resultados. La selección final
preserva candidatos léxicos sin convertirlos artificialmente en coincidencias
vectoriales. El orquestador fija un universo máximo de 200 candidatos y comprueba
que la dimensión del embedding de consulta coincida con el índice.

El orquestador captura el propietario de la base publicada y adquiere una lease
de generación antes de leer. Revalida que el propietario siga vigente, y libera
la generación en `finally`. Filtra fuentes de sesiones que no deben servirse y
solicita después su limpieza condicional; la determinación de esas fuentes se
importa y no está revisada en esta partición.

La política de embeddings propaga cancelación, acota espera entre reintentos y
separa perfiles de retry. El código contempla inicialización, fallback de proveedor
y resultados sólo léxicos. Es observación de la referencia, no una política
adoptada por Jarvis. No se inspeccionaron un motor vectorial, sus binarios/modelos,
exactitud de los embeddings ni la calidad del ranking en datos reales.

Decisión propuesta para Jarvis: modos léxico/semántico/híbrido explícitos;
identidad del motor, modelo, tokenizer, pooling, normalización, dimensión, versión
y hashes ligados al índice. Un proveedor ausente o revocado no habilita otro.
El modo degradado debe ser visible y estar permitido por la elección del dueño.
No llamar semántico al índice FTS actual ni prometer equivalencia por guardar
vectores. La cita debe seguir verificando bytes de la fuente autorizada vigente.

### Indexación automática: parcialmente respondida

B muestra watchers condicionados al corpus elegido y al estado de cierre. Sus
notificaciones marcan generación sucia y desencadenan sincronización; los destinos
se deduplican en una cola compartida. Un lote que falla devuelve sus objetivos a
la cola junto con los que llegaron durante el trabajo. El cierre solicita abortar,
pero espera la retirada real del observador y conserva fallos de limpieza.
La coordinación de generación contempla lectores, escritor y cancelación del
waiter. El comentario limita la coordinación a un Gateway propietario: no acredita
seguridad entre escritores ajenos ni una sandbox del sistema operativo.

Los filtros de rutas, asentamiento de archivos, acceso remoto y el lector real de
fuentes se importan; no quedaron acreditados por analizar estos tres módulos.
No se observó un watcher en ejecución ni se abrió una carpeta del dueño.

Decisión propia: un cambio de archivo es señal de trabajo pendiente, no permiso
para leerlo. Sólo entran textos ya aprobados o fuentes concretas seleccionadas,
ligadas a raíz física, identidad/hash y grant vigente. Deben existir límites de
archivos, bytes, cola y tiempo; deduplicación y cancelación mantienen un único
dueño del trabajo. `source_uri` continúa siendo una etiqueta sin poder de lectura.

### Recuperación antes de responder: parcialmente respondida

C muestra recall ligado a una audiencia y a una sesión interactiva. La política
excluye triggers internos y entregas entre sesiones. La caché incorpora query,
audiencia, fingerprint de autoridad, modelo/herramientas y ámbito de recursos;
una promesa por run evita duplicar recalls. Tras timeout, un reemplazo espera que
recall y limpieza se asienten. Hay límites de caché, TTL y circuit breaker.
El fingerprint se recibe de otro módulo; no se afirma que por sí solo incorpore
la generación del índice o las notas borradas.

La referencia usa un subagente/modelo elegido; sin modelRef devuelve un resultado
vacío identificado. Antes de ejecutar llama a una comprobación de audiencia y
propaga el abortSignal. La persistencia de transcripts es opt-in y se omite en
modo incógnito. El cleanup corre en `finally`, y su fallo invalida el recall.
La elección real de modelo, el prompt, la extracción de evidencia y los imports
de audiencia/transcript no están completos en esta selección.

Decisión propia: recuperar localmente antes de iniciar cada turno, sin incorporar
un segundo proveedor de inferencia. Una cápsula de datos liga run, agente/workspace,
modo temporal, revisión de política, generación de fuentes/índice y citas.
Revalidarla justo antes de enviarla y después de cualquier espera que permita una
revocación. Contenido recuperado y citas son datos; nunca instrucciones ni permisos.
La opción temporal no abre la memoria durable. Los eventos de auditoría conservan
identidades y hashes, no copias nuevas de todo el corpus.

### Mantenimiento y reconstrucción: parcialmente respondida

D conserva generaciones sucias cuando adquirir el manager falla, no existe o el
sync no termina. Un conflicto de revisión puede provocar un único nuevo intento
de esa generación automática. La reconstrucción prepara una base shadow y conserva
la publicada para búsquedas concurrentes. Registra progreso, identidad de proveedor,
revisión original y fuentes que cambian; al publicar pasa expectedRevision e
identidad de la shadow bajo la coordinación de generación. Sólo poda caché después
de publicar con éxito. La implementación de `publishShadow`, validación física y
transacción se importan: este estudio no prueba su atomicidad en ejecución.

El lock de reconstrucción tiene una espera acotada para reset y una cola dentro
del proceso; otro escritor externo no está soportado por ese contrato. El cierre
de shadow y retirada de temporales forman parte de la finalización. No se
ensayaron interrupciones, ENOSPC ni conflictos reales de la referencia.

Decisión propia: índice SQLite derivado separado del almacén de verdad; reconstruir
con presupuestos de bytes/tiempo/espacio y publicar sólo contra revisión/identidad
vigentes. Un fallo o interrupción conserva el índice anterior y los originales.
Nada de copias completas o compactación ilimitadas en cada consulta. Cancelar no
libera la plaza hasta confirmar cierre y limpieza. Borrado/corrección deben dejar
obsoletas las generaciones y cachés antiguas antes de servir resultados; la nueva
indexación no debe resucitar una nota olvidada desde una fuente o trabajo viejo.
La política de tombstones y reimportación requiere código propio y pruebas: no
se inspeccionaron aquí los módulos `memory-forget-*` de OpenClaw.

## Integración propuesta con Jarvis actual

La revisión de fuentes propias previa al OC23 encontró que `memory.sqlite3` ya
mantiene documentos, chunks y FTS de forma transaccional, con ámbitos, revisiones,
hashes y citas. Esquema admitido v1/v2, metadata exacta y recuperación estricta.
No añadir tablas o claves a ese archivo sin migrar y acreditar apertura, copias y
recuperación completas. Un índice semántico derivado puede regenerarse desde
notas/fuentes autorizadas; su ausencia, corrupción o incompatibilidad no modifica
el almacén de verdad. Políticas, fuentes automáticas y grants deben quedar
excluidos o desarmados al restaurar una copia; el índice no conserva permisos.

El transporte actual sólo inyecta `context_data` al crear un hilo. El recall nuevo
necesita un contrato por turno; concatenarlo al contexto de skills no basta para
hilos existentes. Actualizar/borrar una nota invalida la cápsula y el binding
reutilizable. Un fragmento ya entregado al proveedor no desaparece del transcript
por borrar la nota: distinguir eliminación del almacén de memoria de borrado de
historial/cuenta/backups, sin prometer un olvido remoto retroactivo.

Casos de aceptación propuestos para A-24/A-25/A-26:

- Exacto, paráfrasis semántica y combinación híbrida recuperan la fuente correcta
  con citas que resuelven bytes; no presentar similitud como veracidad externa.
- Cambio/borrado y revocación durante una espera excluyen la versión anterior,
  incluso con caché caliente, watcher pendiente y reinicio. No reimportación del
  mismo origen obsoleto; la corrección nueva gana mediante CAS.
- Fuente o política no elegida, namespace ajeno, symlink, credencial, temporal y
  prompt externo que pide persistirse no obtienen lectura/escritura automática.
- Antes de responder en el segundo turno de un hilo existente se entrega sólo
  la cápsula vigente; saltarse el recall o reutilizar un contexto viejo es visible.
- Reconstrucción interrumpida, timeout, conflicto de generación, disco insuficiente
  y cierre incompleto conservan documentos/fuentes e índice publicado anteriores.
- Restaurar una memoria v1/v2 conserva hashes/citas y no reactiva watcher, grants,
  proveedor o red. El índice derivado se comprueba o regenera bajo política nueva.

La selección del motor/modelo local y la autorización de adquirir sus artefactos
siguen siendo una decisión separada del dueño. Este estudio no elige MiniLM,
Ollama ni un servicio remoto, y no autoriza descargas ni respaldos implícitos.
La inferencia elegida continúa siendo Codex con ChatGPT. Las mejoras de fase B
no se incorporan con esta investigación.

## Límites y siguiente partición útil

Quedan sin resolver dependencias de motores/vectores, implementación MMR/decay,
lector/filtrado de fuentes, autoridad final de audiencia, formato de prompts,
publishShadow, borrado/tombstones e índices recuperados tras crash. Son ámbitos
concretos para otro lote si su respuesta resulta necesaria; los 2.907 unknowns
semánticos no equivalen a 2.907 fallos de producto. No se afirma revisión completa
de OpenClaw ni cierre de A-24/A-25/A-26 por este análisis estático.

## Registro legible por máquina

```json
{
  "record_kind": "partial_static_reference_memory_research",
  "date": "2026-10-27",
  "reference_revision": "1e3ff306abaff29f03e7668d53b01c07f10294ac",
  "reference_executed": false,
  "code_imported_to_product": false,
  "local_tests_run": false,
  "model_downloaded": false,
  "embedding_engine_selected": false,
  "analysis_request_limits": {
    "max_heap_mb": 512,
    "analysis_timeout_ms_per_source": 60000,
    "max_semantic_nodes": 30000,
    "max_semantic_nodes_per_file": 10000,
    "max_semantic_unknowns": 18000,
    "max_semantic_unknowns_per_file": 6000,
    "integrity_policy": "fail",
    "detail": "summary"
  },
  "acquisition_path": "/private/tmp/jarvis-rea-oc23-memory-rbu8pjpw/acquisition.json",
  "acquisition_sha256": "306efe6135fe6e5a4f09b2cecbb6f6a713d3117114f12c3f15765ecbdee58970",
  "evidence_bundle_path": "/private/tmp/jarvis-rea-oc23-memory-rbu8pjpw/evidence.json",
  "evidence_bundle_bytes": 17027821,
  "evidence_bundle_sha256": "b265b41078e5a4d614970f2634301dcb09378faafc6cf143aace52f40bc3ad03",
  "evidence_bundle_records": 15,
  "registered_residual_unknowns": 0,
  "parsed_unique_ts_files": 13,
  "semantic_unknowns_sum": 2907,
  "partitions": [
    {
      "id": "OC23A-search",
      "parent_evidence_id": "ev_b530db2ea6e25d3a7d7978517e5908806a2edfcf34c3704ecfde2c577837d17e",
      "summary_evidence_id": "ev_66482b15d81c61bee743cd1779b9440b12c5f9911ad1f0be34be4480b923c21f",
      "trace_evidence_id": "ev_9c90a57a9732c90269f08e1288bfe6bde047de3508af62d7c064fc4bb67981ca",
      "root_artifact_sha256": "5af90f3e30d13e82e84de25c637d437a2b5644ae918804e728c54f73e1879515",
      "statistics": {
        "findings": 85,
        "invalid_utf8_files": 0,
        "modules": 0,
        "nested_asar_containers": 0,
        "parse_failures": 0,
        "parsed_javascript_files": 3,
        "relevant_files": 5,
        "skipped_javascript_files": 0,
        "text_bytes_read": 204265,
        "visited_ast_nodes": 5803
      },
      "application_graph_coverage": "complete",
      "semantic_graph_coverage": "unknown",
      "semantic_unknowns": 1156,
      "trace_summary": {
        "inferred_facts": 22,
        "matched_seeds": 2,
        "native_handoffs": 0,
        "observed_facts": 6,
        "terminal_paths": 0,
        "traced_edges": 12,
        "traced_nodes": 11,
        "unavailable_facts": 0,
        "unknown_facts": 0
      },
      "trace_coverage": {
        "source_graph_status": "complete",
        "status": "complete-within-source",
        "total_seed_matches": 2
      }
    },
    {
      "id": "OC23B-autoindex",
      "parent_evidence_id": "ev_b3ed8cd797c9756d358ddcbe7fa4b441e6d159b0dd4b0a63b43a2661a762c92e",
      "summary_evidence_id": "ev_680f84b7b42f8d5fab1a8d8070731759817358d0d25da37c25d74588dde8e817",
      "trace_evidence_id": "ev_6641884c1a16c3f9faa3cd7a77f95b330538ced5005805b2cde765371234a7b1",
      "root_artifact_sha256": "37ae70659011849af95c6f8c96c85f3c80d0e6455d0159946b8919a3ac29056b",
      "statistics": {
        "findings": 19,
        "invalid_utf8_files": 0,
        "modules": 0,
        "nested_asar_containers": 0,
        "parse_failures": 0,
        "parsed_javascript_files": 3,
        "relevant_files": 5,
        "skipped_javascript_files": 0,
        "text_bytes_read": 168365,
        "visited_ast_nodes": 1860
      },
      "application_graph_coverage": "partial",
      "semantic_graph_coverage": "unknown",
      "semantic_unknowns": 361,
      "trace_summary": {
        "inferred_facts": 8,
        "matched_seeds": 2,
        "native_handoffs": 0,
        "observed_facts": 5,
        "terminal_paths": 0,
        "traced_edges": 5,
        "traced_nodes": 6,
        "unavailable_facts": 0,
        "unknown_facts": 0
      },
      "trace_coverage": {
        "source_graph_status": "partial",
        "status": "partial",
        "total_seed_matches": 2
      }
    },
    {
      "id": "OC23C-recall",
      "parent_evidence_id": "ev_9bcfd8202741ec92f7b58e4d3bcecbe9fd07809480934bdd0d1c764fc72314f9",
      "summary_evidence_id": "ev_b6454145ed0e8daafb6267fda02a7fb5fc557724ec4971958c3c12c48e1923d8",
      "trace_evidence_id": "ev_b9d854af7c416d3244a96259733d33ff198e7de744bface6f4982b0c45575eff",
      "root_artifact_sha256": "145bd9a336ce8d980a67f18064d5615ee514f071498aaf9246fe0c5dc980dd8d",
      "statistics": {
        "findings": 174,
        "invalid_utf8_files": 0,
        "modules": 0,
        "nested_asar_containers": 0,
        "parse_failures": 0,
        "parsed_javascript_files": 4,
        "relevant_files": 6,
        "skipped_javascript_files": 0,
        "text_bytes_read": 206451,
        "visited_ast_nodes": 5252
      },
      "application_graph_coverage": "partial",
      "semantic_graph_coverage": "unknown",
      "semantic_unknowns": 1049,
      "trace_summary": {
        "inferred_facts": 226,
        "matched_seeds": 2,
        "native_handoffs": 0,
        "observed_facts": 32,
        "terminal_paths": 0,
        "traced_edges": 120,
        "traced_nodes": 79,
        "unavailable_facts": 0,
        "unknown_facts": 0
      },
      "trace_coverage": {
        "source_graph_status": "partial",
        "status": "partial",
        "total_seed_matches": 2
      }
    },
    {
      "id": "OC23D-maintenance",
      "parent_evidence_id": "ev_0d8e3689321c4a12717190ecc51e004f1e4fc6cf9f614969925f5d3879a9f810",
      "summary_evidence_id": "ev_01d742b6a068bc9aebf793519f1f3be7a9da5b360c0e7344f429957cbd11fb10",
      "trace_evidence_id": "ev_add94b76f717a13b70ef82f57c26db305d59aa40b6493ca66b03a9eef0626c6a",
      "root_artifact_sha256": "e6b9bda47ff12de8df014eb7e4891041a89548800b940686d7a4bf77e93b4482",
      "statistics": {
        "findings": 63,
        "invalid_utf8_files": 0,
        "modules": 0,
        "nested_asar_containers": 0,
        "parse_failures": 0,
        "parsed_javascript_files": 3,
        "relevant_files": 5,
        "skipped_javascript_files": 0,
        "text_bytes_read": 171424,
        "visited_ast_nodes": 1801
      },
      "application_graph_coverage": "complete",
      "semantic_graph_coverage": "unknown",
      "semantic_unknowns": 341,
      "trace_summary": {
        "inferred_facts": 18,
        "matched_seeds": 2,
        "native_handoffs": 0,
        "observed_facts": 4,
        "terminal_paths": 0,
        "traced_edges": 10,
        "traced_nodes": 11,
        "unavailable_facts": 0,
        "unknown_facts": 0
      },
      "trace_coverage": {
        "source_graph_status": "complete",
        "status": "complete-within-source",
        "total_seed_matches": 2
      }
    }
  ]
}
```
