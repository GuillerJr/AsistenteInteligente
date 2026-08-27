# ADR-0127: compuerta privada de calidad conversacional

- Estado: aceptado
- Fases: 1, 2, 3 y 5

## Decisión

1. Toda respuesta completada sin herramientas se evalúa en memoria mediante reglas deterministas;
   no se realiza otra inferencia local o NVIDIA.
2. La evaluación conserva únicamente modo de diálogo, puntuación, aprobación, cantidad de palabras
   y frases, y un conjunto cerrado de banderas. Solicitud y respuesta no forman parte del contrato
   persistente.
3. Se detectan eco literal, repetición exacta, exceso respecto al presupuesto del modo, apertura
   prefabricada, afirmación de identidad humana y lenguaje explícito de dependencia relacional.
4. Una puntuación menor de 85 falla la evaluación individual. El estado agregado `competitive`
   exige que al menos 95 % de las respuestas evaluadas la superen, además de los objetivos de
   éxito y latencia existentes.
5. Las filas históricas que no contienen estos campos conservan compatibilidad y no se cuentan en
   el denominador de calidad conversacional.

## Motivo

Éxito técnico y baja latencia no demuestran que Jarvis converse bien. Una compuerta pequeña permite
detectar regresiones observables sin almacenar conversaciones, pagar otra llamada o incorporar una
dependencia de evaluación.

## Límites

- Las banderas son señales operativas, no evaluación psicológica ni verdad semántica.
- La métrica no cambia modelos, permisos, memoria o políticas automáticamente.
- No intenta acortar respuestas de forma destructiva después de generarlas.
- Acciones con herramientas conservan su evaluación de postcondición independiente.
