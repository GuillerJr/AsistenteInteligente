# ADR-0061: Contenido de memoria verificado

- Estado: aceptado
- Fase: 2 — Memoria persistente
- Fecha: 2026-08-21

## Contexto

Memorias y turnos almacenaban `content_sha256`, pero las rutas de lectura copiaban contenido y hash
sin recomputarlo. Una edición accidental o no coordinada podía entrar en RAG, historial y respuestas
IPC con un digest obsoleto.

## Decisión

`get`, búsqueda FTS/vectorial y recuperación de conversaciones recomputan SHA-256 sobre el contenido
completo antes de construir cualquier contrato público o extracto. Una diferencia produce
`MemoryStoreError`; el servicio IPC la reduce a `memory_unavailable` y no devuelve contenido.

La conversión de filas también normaliza errores de tipos, timestamps, tags y contratos persistidos
como fallos internos de memoria, no como payload inválido del cliente.

## Filtro del algoritmo de ingeniería

1. Se cuestionó que almacenar un hash equivaliera a verificarlo.
2. Se descartaron migración, tabla adicional y reindexación.
3. Se reutiliza el SHA-256 ya calculado al escribir.
4. El costo se limita a los contenidos que realmente salen de SQLite.
5. Todas las rutas públicas convergen en tres conversores verificados.

## Consecuencias

La corrupción o edición que no actualiza el digest falla cerrada antes de alcanzar al modelo. SHA-256
sin clave no prueba autenticidad frente a un actor capaz de reescribir simultáneamente contenido y
hash; una MAC durable requiere gestión de claves y queda fuera de este incremento.
