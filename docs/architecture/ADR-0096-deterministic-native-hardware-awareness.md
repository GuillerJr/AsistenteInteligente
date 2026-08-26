# ADR-0096: conciencia nativa y determinista del hardware

- Estado: aceptado
- Fases: 1, 4 y 5

## Decisión

1. Las frases exactas `Describe este Mac`, `¿Qué Mac tengo?` y equivalentes acotados crean localmente
   una llamada `system_describe_runtime` sin recuperar memoria ni invocar un modelo.
2. El ejecutor conserva arquitectura, sistema, versión de macOS, release y Python. En Darwin añade
   chip, modelo de hardware y memoria mediante una única orden fija `/usr/sbin/sysctl -n`.
3. La consulta nativa no usa shell, entrada del usuario ni entorno heredado; descarta `stdin`, limita
   salida a tres valores validados y vence al segundo.
4. Si `sysctl` falta, falla o devuelve datos inválidos, los metadatos de hardware se omiten y la
   lectura base sigue siendo exitosa.
5. El grafo valida el resultado y construye una frase española determinista. Publica el mismo texto
   como primer fragmento y resultado final, con modelo atribuido `local/deterministic-runtime`.

## Motivo

Preguntar a un LLM qué hardware posee el equipo agrega latencia y puede producir una respuesta
inventada. macOS ya expone la fuente autoritativa; leerla directamente es más rápido y verificable.
En este Mac la fuente reportó Apple M5, Mac17,3 y 16 GiB. En 500 ejecuciones simuladas del grafo se
observaron cero rondas Apple, cero rondas NVIDIA y 2,47 ms promedio de overhead interno. La consulta
nativa fue sustituida por un doble para aislar la orquestación.

## Límites

- Solo se exponen metadatos no secretos; no se consultan número de serie, UUID, red ni almacenamiento.
- Los valores textuales se limitan a 128 caracteres y la memoria a un rango plausible de 1 GiB a
  2 TiB.
- La respuesta determinista solo se usa para una llamada directa exitosa con contrato válido.
- Consultas abiertas sobre rendimiento, diagnóstico o ciberseguridad conservan sus especialistas y
  políticas existentes.
