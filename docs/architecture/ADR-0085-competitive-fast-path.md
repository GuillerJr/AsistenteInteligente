# ADR-0085: camino rápido competitivo y objetivos de calidad

- Estado: aceptado
- Fases: 1, 2, 3 y 4

## Decisión

1. Una solicitud elegible para Apple Foundation Models recupera memoria mediante SQLite/FTS5 y el
   perfil local. La memoria semántica on-device tampoco consulta Keychain ni NVIDIA.
2. Memoria episódica y perfil del propietario se recuperan en paralelo. Cuando se usa RAG híbrido,
   la búsqueda léxica y el embedding de consulta también comienzan en paralelo.
3. Un fallo del cerebro local anterior al primer delta abre un cortacircuito de 30 segundos. Los
   turnos dentro de esa ventana pasan directamente a NVIDIA. Un fallo después de publicar texto
   termina el turno para evitar concatenar dos respuestas distintas.
4. La autoevaluación de sesión publica un marcador sin contenido con estos objetivos iniciales:
   éxito mínimo de 95 %, primer fragmento p95 menor o igual a 2 segundos y conversación completa
   p95 menor o igual a 8 segundos. El estado solo puede ser `competitive` con al menos 20 trabajos;
   antes se informa `insufficient_data`.
5. Las llamadas de herramienta se atribuyen a la evaluación del job, incluidas las herramientas
   de lectura que no requieren confirmación. Su tasa de éxito se informa separada de la conversación.

## Motivo

El camino común debe ser privado y rápido por construcción, no solo por configuración. Medir p50
oculta pausas perceptibles; p95 expone los turnos lentos que degradan la confianza. El cortacircuito
elimina reintentos inútiles sin incorporar otra dependencia ni un servicio de salud adicional.

## Límites

- Los objetivos son observabilidad, no autorización para cambiar modelos o políticas.
- No se persisten prompts, respuestas, argumentos, identidades ni métricas entre reinicios.
- El marcador no se declara competitivo con una muestra menor a 20 trabajos.
- La recuperación local conserva los mismos límites de namespace, cantidad y bytes del RAG.
