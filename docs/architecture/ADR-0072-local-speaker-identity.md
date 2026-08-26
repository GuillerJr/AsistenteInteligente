# ADR-0072: Identidad local y falible del hablante

- Estado: aceptado
- Fases: 3 y 5

## Decisión

La identificación reutiliza los buffers efímeros del turno de Apple Speech con SoundAnalysis y un
clasificador Core ML compilado para CPU y Neural Engine. El modelo debe contener `background` y de
uno a ocho identificadores seguros. Con un perfil funciona como verificación local frente a la clase
de fondo, que debe incluir voces no enroladas. Se requieren dos observaciones, confianza media mínima de 0,78
y margen medio mínimo de 0,12; cualquier ausencia o activo inválido falla de forma cerrada sin
interrumpir la transcripción.

El modelo se entrena localmente mediante Create ML con un dataset externo y privado, límites por
clip/clase, split fijo y error de validación máximo de 25 %. Las grabaciones no entran al bundle ni
al repositorio; solo se empaqueta `JarvisSpeakerIdentity.mlmodelc` después de validarlo.

## Límite de seguridad

La etiqueta y su confianza son una pista de personalización. No autentican al usuario, no conceden
capacidades, no aprueban herramientas y no modifican las políticas. El modelo puede equivocarse,
especialmente con ruido, grabaciones o voces no enroladas.

## Consecuencias

- Fase 3 añade identidad sin un segundo stream, servicio remoto ni retención de PCM.
- Fase 5 muestra la última voz identificada en Menu Bar cuando existe.
- El flujo queda operativo al entrenar el activo local; sin modelo, Jarvis mantiene toda la voz
  existente y omite la identidad.
