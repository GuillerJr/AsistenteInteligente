# ADR-0197: cascada por confianza, memoria de bajo perfil y sensor térmico

- Estado: aceptado
- Fecha: 2026-08-28
- Fases: 1, 2, 3 y 5
- Reemplaza: ADR-0121 y ADR-0143

## Requisitos cuestionados

El target real es macOS 26 en un MacBook Air M5 sin ventilador. La API de Apple para
`PrivateCloudComputeLanguageModel` no pertenece a ese target y requiere un entitlement administrado;
simularla con una URL privada sería frágil y rompería el límite de confianza. Del mismo modo,
descargar otro modelo de embeddings de hasta 80 MB es innecesario mientras NaturalLanguage ya ofrece
un embedding on-device sin artefactos del proyecto.

## Decisión

1. Toda consulta de texto no determinista intenta primero el modelo `foundation` del wrapper local
   fijado a loopback. El helper Foundation Models firmado permanece como segundo acceso local.
2. La confianza combina probabilidad del token elegido y entropía normalizada. Valores menores a
   `0.82`, ausencia de logprobs y planes con herramientas escalan a `openai/gpt-oss-20b`; código,
   ciberseguridad y razonamiento crítico escalan a
   `deepseek-ai/deepseek-v4-flash-0731`.
3. Un HTTP 429 abre un cooldown monotónico global de al menos cinco segundos para todos los clientes
   NVIDIA vivos. La respuesta Apple ya calculada se devuelve y la selección se audita sin contenido.
4. La recuperación mantiene FTS5 y RRF. El modo predeterminado usa el embedding NaturalLanguage del
   sistema; el operador puede autorizar NVIDIA con
   `AEGIS_MEMORY_REMOTE_EMBEDDINGS_ENABLED=true`. Los dos modos guardan BLOB `float32` y usan
   `sqlite-vec` con máximo estricto de 2.000 recuerdos recientes.
5. Toda construcción de `MemoryRecord` o `MemorySearchHit` recalcula `content_sha256`. Una diferencia
   invalida la operación completa.
6. `AcousticSensor` observa `thermalStateDidChangeNotification` sin polling. Presión `serious` o
   `critical` libera SoundAnalysis y AVAudioEngine; `nominal` o `fair` permite rearmar mediante las
   compuertas existentes.
7. Dos clasificaciones consecutivas `jarvis` con confianza mínima `0.85` interrumpen Magpie,
   cancelan el job por UDS HMAC y registran `voice_interruption`. Falta de autenticación, cancelación
   o auditoría impide iniciar el nuevo turno.

## Private Cloud Compute

PCC queda fuera del binario macOS 26 por disponibilidad de plataforma, no como implementación
pendiente oculta. El contrato `HybridBrainClient` mantiene separadas las cargas local y redactada
remota, de modo que un cliente PCC nativo podrá insertarse antes de NVIDIA cuando el target, SDK y
entitlement sean válidos. Hasta entonces el orden efectivo es Apple on-device → NVIDIA NIM.

## Consecuencias

Jarvis no retiene un LLM o modelo de embeddings adicional en RAM, evita trabajo térmico durante
presión elevada y limita el índice semántico a unos pocos megabytes. FTS5 sigue respondiendo ante
fallos de embeddings. La auditoría encadenada permite reconstruir rutas, pausas térmicas e
interrupciones sin guardar prompts, respuestas, audio o memoria personal.
