# ADR-0131: métrica privada de recuperación tras reparación

- Estado: aceptado
- Fases: 1, 2, 3 y 5

## Decisión

1. El job que consume una ventana de ADR-0130 registra `repair_attempt=true` en su evaluación.
2. El feedback explícito posterior continúa actualizando ese mismo job con `helpful` o `unhelpful`;
   no se crea un correlacionador, una tabla ni otra inferencia.
3. `repair_recovery_rate` divide reparaciones útiles entre reparaciones calificadas. Se publica
   desde la primera muestra y entra en la compuerta competitiva desde tres calificaciones, con un
   objetivo mínimo de 80 %.
4. La evaluación persiste solo el booleano y el enum. No almacena solicitud, respuesta,
   `conversation_id`, identidad, audio ni la señal interna usada por el prompt.
5. La métrica sobrevive al reinicio mediante el almacén de evaluaciones existente. La ventana de
   reparación continúa siendo efímera y no se reconstruye.

## Motivo

La tasa general de respuestas útiles no distingue una primera respuesta correcta de una corrección
efectiva. Medir la recuperación explícita cierra el ciclo de calidad y detecta si Jarvis repite el
error después de que el propietario explicó qué necesitaba.

## Límites

- Una reparación sin calificación aparece como intento, pero no entra en el denominador.
- La métrica refleja únicamente feedback explícito del propietario; no infiere satisfacción.
- El umbral no modifica prompts, modelos, memoria, permisos ni routing automáticamente.
- Los registros anteriores siguen siendo válidos y usan `repair_attempt=false` por defecto.
