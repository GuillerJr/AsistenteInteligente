# ADR-0088: métrica privada de reconocimiento del propietario

- Estado: aceptado
- Fases: 2, 3 y 5

## Decisión

1. Cada evaluación terminal indica únicamente si el origen fue voz y si esa voz coincidió con el
   único perfil local por encima del umbral existente de 0,78.
2. La autoevaluación calcula `owner_recognition_rate` para los turnos de voz y fija un objetivo
   inicial de 90 %. Los turnos de texto no participan en esa tasa.
3. No salen por IPC el identificador del hablante, la confianza, el embedding, muestras de audio ni
   el transcript. El booleano agregado por turno puede persistir bajo la retención de ADR-0125.
4. El reconocimiento continúa siendo personalización y diagnóstico, nunca autorización. Toda
   acción conserva broker, política y confirmación independientemente de la coincidencia de voz.

## Motivo

Un asistente voice-first no puede mejorar una señal que no mide. La tasa binaria permite detectar
un perfil mal entrenado o degradado sin convertir datos biométricos en telemetría ni aumentar la
autoridad del modelo.

## Límites

- Una coincidencia no demuestra identidad legal ni presencia exclusiva.
- Una no coincidencia impide aprender preferencias de ese turno, pero no revela quién habló.
- El objetivo competitivo solo participa cuando la sesión contiene turnos de voz.
