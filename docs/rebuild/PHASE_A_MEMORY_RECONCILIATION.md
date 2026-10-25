# Fase A: copia explícita de memoria conservada

El núcleo y la compatibilidad SQLite están integrados. El coordinador del host,
su corte de mutación y la interfaz nativa siguen en desarrollo privado. Este
documento no acredita una copia desde la app, una decisión real del dueño ni el
cierre de A-71 o de la fase A. La fase B no ha comenzado.

## Contrato del núcleo

`memory_scope_summaries` y `review_memory_scope_copy` inspeccionan imágenes RAM
acotadas, sin abrir la fuente con SQLite, construir stores ni migrar archivos.
Sólo se admiten ámbitos duraderos del mismo agente; un ámbito ligado a sesión
queda excluido sin denominarlo automáticamente memoria temporal. La revisión fija
identidad física de la base, generaciones, huellas, destino vacío y límites.

`MemoryStore.copy_scope` requiere un contexto de un uso que liga operación,
digest de la revisión del host y digest semántico del núcleo. El llamador debe
aportar la autoridad central y mantener el corte del perfil, agente y workspace;
el contexto por sí solo no acredita autorización. Los metadatos internos de ámbito
no deben viajar sin filtrar al cliente ni al modelo.

La copia conserva originales y crea IDs nuevos con revisión 1. Mantiene texto,
clave, título y URI exactos; la URI sigue siendo una etiqueta no verificada y no
se abre. Una sola transacción incluye upgrade v1→v2 cuando proceda, documentos,
chunks/FTS, generación destino, mapa de procedencia y recibo SQL. La lectura y las
operaciones normales de una memoria v1 no la actualizan automáticamente.

La autoridad se comprueba antes de abrir la base fría y de cada efecto, incluido
COMMIT. Cancelación o fallo antes del commit revierten todo el lote, incluido DDL y
versión. Un COMMIT despachado seguido de confirmación perdida conserva `unknown`;
no se repite la copia. Una apertura intentada puede haber creado locks o cambiado
políticas de conexión aunque no se confirmase ninguna copia de ámbito.

La limpieza incierta conserva el propietario y bloquea el uso normal del store.
El host debe retener el mismo corte hasta terminar realmente la limpieza. Ésta
sólo asienta rollback y políticas propias; nunca COMMIT ni una segunda copia.
La consulta del registro SQL observa filas actuales, no prueba un ACK histórico.
Una edición o eliminación legítima puede hacer que los documentos actuales ya
no coincidan, sin invalidar la historia ni convertirla en autorización.

Los límites incluyen texto, documentos, chunks, historia y trabajo SQL. El límite
físico usa `max_page_count` y el tamaño real antes de COMMIT; el host debe derivar
además el presupuesto agregado con las fuentes hermanas y sus límites de filas.
No se truncan ni resumen datos para hacerlos caber. SQLite utiliza la política de
durabilidad comprobada durante la copia; esto no certifica el hardware frente a
cortes eléctricos. Lecturas de sistema bloqueadas no se vuelven interrumpibles
por añadir un timeout de Python.

## Respaldo, diagnóstico y nueva restauración

El inspector acepta exactamente memoria v1 y v2, con esquemas, contenido, historia
y presupuestos validados en RAM. Otros roles mantienen sus versiones anteriores.
Doctor valida que la versión inspeccionada coincida con la cabecera seleccionada.

La historia no tiene dependencias de documentos, ámbitos o agentes que después
pueden cambiar o desaparecer. Cada recibo se valida contra su mapa inmutable:
agente, ámbito duradero distinto, IDs nuevos, ordinales, cantidades, hashes y bytes.
En otra restauración se conserva ese registro como `restored_history`, sin
transformarlo en operación local, permiso o idempotencia. La transformación ocurre
en la copia RAM; no reescribe el respaldo original. Otro perfil sigue necesitando
su propia activación explícita.

## Pruebas y alcance pendiente

El núcleo privado pasó 153 casos: 43 nuevos y 110 de memoria anterior. La
compatibilidad pasó 212 casos con 32 nuevos, incluido un roundtrip producido por
la copia real del núcleo: backup, verificación, nueva restauración, activación de
fixture y resolución de citas. El árbol integrado pasó 494 pruebas focalizadas,
Ruff, bytecode y revisión de diferencias. Los gates globales y CI del checkpoint
se registrarán después de la publicación.

Los casos cubren UTF-8, IDs y citas, origen intacto, destino con tombstone, cambios
físicos, contextos no prestables, límites, fallo SQL a mitad de lote, SQLITE_FULL,
rollback de DDL/FTS/versión/recibo y confirmación perdida tras COMMIT. También se
comprueban propietario retenido, historial posterior a edición/borrado, lectura
v1 sin migración y rechazo de SQL de esquema sobredimensionado antes de analizarlo.
Todas las decisiones de estos fixtures son sintéticas.

Faltan el host y su autoridad/corte integrados, sus presupuestos agregados, los
DTOs y el panel nativo, las carreras de RPC/cierre y la copia de dos notas de
prueba desde la app con aprobación del dueño. Las dos notas se prepararon como
datos sintéticos, con revisiones 2 y 1; no se ha ejecutado esa copia ni simulado
su aprobación. WAL frío, recuperación de archivos de workspace, otros canales y
la distribución autocontenida continúan pendientes dentro de la fase A.

La investigación estática que orienta estas decisiones es
[OC-17](REA_PART17_SUMMARY.json), con [procedencia](REA_PART17_PROVENANCE.json).
No se importa ni ejecuta código de OpenClaw y sus helpers no examinados siguen
siendo desconocidos.
