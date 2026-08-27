# ADR-0129: feedback privado sobre la respuesta anterior

- Estado: aceptado
- Fases: 1, 2, 3 y 5

## Decisión

1. Solo frases explícitas y completas clasifican la respuesta anterior como `helpful` o
   `unhelpful`; preguntas y comentarios ambiguos se ignoran.
2. El veredicto apunta al último job completado, no dedicado a feedback, con el mismo
   `conversation_id`. No cruza conversaciones y no reconstruye asociaciones después de reiniciar.
3. En voz se exige el único propietario verificado. Texto conserva el supuesto de sesión local.
4. El acuse usa un fast path determinista y no invoca modelos. La actualización del job objetivo y
   la finalización del turno de feedback ocurren bajo la misma exclusión local.
5. `JobEvaluation` persiste solo el enum del veredicto. Solicitud, respuesta, identidad y audio no
   forman parte de la evaluación.
6. Los eventos de feedback no se cuentan en latencia o calidad conversacional. Con al menos cinco
   respuestas calificadas, el estado `competitive` exige una tasa útil mínima de 80 %.

## Motivo

Las heurísticas detectan forma, pero solo el propietario puede confirmar utilidad. Un veredicto
binario explícito añade evidencia longitudinal sin guardar conversación, ejecutar otra inferencia o
confundir un acuse rápido con una mejora del cerebro.

## Límites

- Una calificación no cambia modelos, prompts, permisos o memoria por sí sola.
- Si el target fue expulsado o el daemon se reinició, Jarvis falla cerrado y lo informa.
- Repetir una calificación reemplaza el veredicto del mismo job; no crea votos duplicados.
- Herramientas conservan además su postcondición verificada independiente.
