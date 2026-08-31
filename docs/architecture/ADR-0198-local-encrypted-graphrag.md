# ADR-0198: GraphRAG local cifrado y acotado

## Estado

Aceptado.

## Decisión

La memoria semántica deja de clasificar recuerdos completos mediante un índice vectorial plano y
RRF. SQLite conserva FTS5 como respaldo lexical y añade un grafo de propiedades con tablas `nodes`
y `edges`. Cada recuerdo crea un nodo `document`; un extractor determinista offline enlaza personas,
proyectos, herramientas y conceptos. Los nombres y propiedades se cifran con la misma clave aislada
de memoria y se consultan mediante índices ciegos, por lo que no queda PII en columnas públicas.

`node_embeddings` usa `sqlite-vec` con vectores `float32[384]`. El proveedor es exclusivamente Apple
NaturalLanguage on-device. La reducción determinista a 384 dimensiones mantiene el mismo espacio
para indexación y consulta. Un FIFO de 2.000 entradas por namespace elimina solamente vectores;
nunca borra recuerdos FTS5, nodos ni relaciones.

La consulta selecciona entre tres y cinco semillas KNN. Un Personalized PageRank bidireccional con
damping 0,85 ejecuta siete iteraciones sobre un máximo de 4.096 nodos y 16.384 aristas. Solo el
subgrafo local acotado a 4 KiB entra al contexto del cerebro local. Ningún nombre, propiedad,
consulta o resumen GraphRAG se envía a NVIDIA.

`sqlite-vec 0.1.8` tiene un defecto de borrado cuando una tabla virtual contiene metadatos de texto
largos. Para conservar el FIFO físico, la tabla virtual guarda únicamente el UUID, una partición
entera derivada del namespace y el vector. Modelo, hash y timestamps residen en una tabla SQLite
STRICT autenticada contra el nodo. Esto evita la ruta defectuosa sin relajar aislamiento.

## Consecuencias

- El índice vectorial queda por debajo de 3 MiB de floats por namespace.
- FTS5 sigue respondiendo cuando el helper Apple no está disponible.
- La ausencia de `sqlite-vec` impide abrir el esquema GraphRAG y falla cerrada.
- Se elimina el comando de soak específico del índice plano; la telemetría reporta conteo de nodos
  embebidos y estimación exacta de bytes, sin texto de usuario.
- La migración v5 descifra cada recuerdo dentro de una transacción local, crea su subgrafo cifrado,
  elimina `memory_embeddings` y establece `user_version=6`.
