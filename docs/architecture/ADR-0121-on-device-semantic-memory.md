# ADR-0121: memoria semántica on-device con respaldo FTS5

- Estado: reemplazado por ADR-0197

## Contexto

La coincidencia léxica de FTS5 es rápida y privada, pero no recupera bien recuerdos expresados con
sinónimos. Enviar memoria personal a un proveedor remoto contradice el límite local-first.

## Decisión

1. Un helper Swift mínimo obtiene el embedding español incluido en `NaturalLanguage` de macOS.
2. El protocolo por stdin/stdout acepta como máximo 16 textos, 16 KiB por texto y 128 KiB por lote.
3. El daemon valida propietario, permisos, modelo, dimensiones, números finitos y normaliza vectores.
4. La búsqueda léxica FTS5 y el embedding de consulta se ejecutan en paralelo; Reciprocal Rank
   Fusion combina ambos rankings.
5. Fallo, timeout o ausencia del helper devuelven inmediatamente el ranking FTS5.
6. Un único backfill en segundo plano procesa hasta 500 recuerdos recientes en lotes de siete. El
   SHA-256 del contenido y el identificador de modelo evitan reutilizar índices obsoletos.
7. Esta ruta no usa la API NVIDIA y no ofrece una variable para habilitar embeddings remotos.

## Consecuencias

La memoria entiende similitud semántica sin sacar preferencias del Mac ni añadir un modelo
descargado por el proyecto. La disponibilidad concreta depende de los assets lingüísticos de macOS;
FTS5 conserva funcionalidad determinista cuando NaturalLanguage no está disponible.
