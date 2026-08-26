# ADR-0090: camino determinista para acciones inequívocas

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Decisión

1. Una gramática local exacta convierte tres clases acotadas en una única `ToolCall`: apertura de
   aplicaciones incluidas en una lista de bundle IDs conocidos, ejecución de un atajo con nombre
   literal y selección de uno de los cuatro diagnósticos nativos fijos.
2. El nodo se ejecuta después del routing y antes de RAG. Si reconoce la orden, omite memoria,
   embeddings, Apple Foundation Models y NVIDIA NIM.
3. La llamada entra al mismo `ToolBroker`. Como todas estas operaciones son de riesgo alto o
   crítico, el grafo termina en `awaiting_confirmation`; aprobación, consumo atómico, ejecución y
   auditoría no cambian.
4. Órdenes compuestas, negadas, desconocidas, multimodales, nombres de atajo inválidos y solicitudes
   con `force_remote` no entran al camino directo.
5. La autoevaluación atribuye estas solicitudes al cerebro `deterministic`, no a NVIDIA.

## Motivo

Usar un modelo remoto para transformar «Abre Safari» en `com.apple.Safari` añade red, tokens y una
posibilidad de alucinación sin aportar razonamiento. Una lista pequeña y una gramática exacta reducen
el tiempo hasta la aprobación y producen argumentos estables sin ampliar autoridad.

## Límites

- La lista de aplicaciones no se descubre ni amplía automáticamente.
- La gramática no resuelve pronombres, contexto conversacional ni más de una acción.
- El nombre de un atajo sigue siendo dato no confiable y se valida de nuevo en el broker.
- Reconocer una orden prepara una confirmación; nunca equivale a aprobarla o ejecutarla.
