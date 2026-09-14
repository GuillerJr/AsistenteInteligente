# ADR-0222: continuidad conversacional y evaluación coherente

Fecha: 2026-09-14. Estado: aceptado.

## Decisión

La política de diálogo concentra el presupuesto de frases, el máximo de una pregunta útil y la
prioridad de la corrección actual sobre suposiciones anteriores. El especialista y el sintetizador
usan el mismo contrato. Se conserva el prompt específico del CLI de ingeniería NVIDIA, sin
aplicarle límites propios de voz.

La clasificación local ignora citas/código, distingue saludos solos de saludos con tareas y reconoce
correcciones habituales. Conserva puntuación para distinguir negación de cambio de cláusula. Esta
clasificación ajusta estilo: no autentica, autoriza herramientas ni registra estados emocionales.

La síntesis local incorpora el historial reciente con los mismos límites de texto/voz del
especialista. Conserva únicamente las preferencias explícitas ya recuperadas y protege historial,
contexto relacional y reparación con la compuerta de privacidad del propietario. No se añade otra
búsqueda de memoria ni inferencia. El contexto sigue siendo dato no confiable. La síntesis con
herramientas mantiene prohibido el fallback remoto; el payload NVIDIA ordinario no incorpora
historial, preferencias ni la ventana de reparación.

Una voz no verificada no consume una reparación pendiente. El plazo de dos minutos y el consumo
único al admitir el siguiente turno autorizado permanecen. En el cliente nativo se inspeccionan
preguntas y comillas antes de normalizar una orden de reinicio; citarla o preguntarla no rota sesión.

La evaluación posterior añade `empty_response` y `excessive_questions`. El conteo conserva el
contenido de enlaces/código pero ignora sus delimitadores internos de frase, así como puntos de
versiones, decimales y abreviaturas comunes. La detección de afirmaciones humanas respeta límites
de cláusula en las negaciones. El contrato de métricas admite el catálogo completo de banderas y
mantiene compatibles las evaluaciones anteriores, sin migrar el esquema ni guardar texto.

## Límites y evidencia

Las reglas son heurísticas y no prueban verdad, empatía, ausencia de alucinaciones o comprensión
universal del idioma. No existe un nuevo filtro generativo, llamada de evaluación o reintento.
Tampoco se habilitan micrófono, captura continua, permisos o persistencia adicional.

Las regresiones y comandos reproducibles constan en
[CONVERSATION_POLISH.md](../quality/CONVERSATION_POLISH.md).
