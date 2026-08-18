# ADR-0005: recuperación híbrida y contexto RAG no confiable

- Estado: aceptado
- Fecha: 2026-08-18

## Contexto

FTS5 ofrece recuperación local determinista, pero no relaciona consultas y recuerdos que expresan
el mismo concepto con vocabulario diferente. El proyecto consume modelos mediante API NVIDIA NIM y
no descargará un modelo de embeddings al Mac. Enviar memoria persistida a un tercero, sin embargo,
es una decisión de privacidad que no puede activarse implícitamente.

## Decisión

1. Se utilizará `nvidia/nemotron-3-embed-1b` mediante `POST /v1/embeddings`, con `passage` al
   indexar y `query` al recuperar. Los vectores del proveedor se validarán, normalizarán y limitarán
   antes de persistirse.
2. Los embeddings remotos estarán desactivados por defecto. Solo
   `AEGIS_MEMORY_REMOTE_EMBEDDINGS_ENABLED=true` autorizará enviar contenido y consultas al endpoint
   NVIDIA. El modo local continuará plenamente funcional.
3. SQLite seguirá siendo la fuente de verdad. El esquema v2 almacenará vectores `float32`, modelo,
   dimensión y hash del contenido; una migración transaccional conservará todos los registros v1.
4. La búsqueda semántica calculará coseno sobre un máximo configurable de registros recientes,
   inicialmente 2.000, para acotar CPU y memoria en el MacBook Air. No se cargará una extensión
   binaria ni un modelo local.
5. Los rankings FTS5 y vectorial se combinarán mediante Reciprocal Rank Fusion. Si NVIDIA no está
   disponible, se devolverá el ranking léxico sin propagar detalles del proveedor.
6. El routing ocurrirá antes de recuperar memoria, de modo que contenido persistido no pueda alterar
   la selección de agente. El namespace será configuración del daemon y nunca un valor controlado
   por el prompt.
7. El especialista recibirá como máximo 4 KiB de extractos serializados como datos no confiables,
   junto con una instrucción de ignorar órdenes presentes en ellos. La memoria no podrá conceder
   herramientas, modificar políticas ni sustituir la solicitud actual.

## Consecuencias

- El asistente obtiene recuperación multilingüe y semántica sin descargar modelos al M5.
- La indisponibilidad o cuota del endpoint gratuito reduce calidad de recuperación, pero no impide
  responder con el índice local.
- Los registros creados con embeddings desactivados requieren una futura tarea explícita de
  reindexación si el operador habilita después el modo remoto.
- El escaneo vectorial acotado es apropiado para esta etapa; un índice ANN nativo requerirá una ADR
  y una evaluación separada de binarios arm64, mantenimiento y superficie de ataque.
