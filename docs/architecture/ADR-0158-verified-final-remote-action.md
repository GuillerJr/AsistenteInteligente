# ADR-0158: inspección de la última acción remota

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

El ciclo capturaba al inicio de cada paso. Si NVIDIA actuaba en el último paso permitido, la sesión
terminaba como `step_limit` antes de volver a observar. El efecto final podía quedar sin comprobar y
el contenido sensible revelado no pasaba por la compuerta posterior.

## Decisión

1. Después de toda acción remota, el controlador estabiliza y recaptura inmediatamente la aplicación
   autorizada.
2. La recaptura comprueba `secure_content` antes de cualquier otra decisión. Una transición sensible
   termina como `blocked/sensitive_action` y cuenta la acción ya ejecutada.
3. La observación segura se conserva solo en memoria y sustituye la captura inicial del siguiente
   paso. El camino normal mantiene una captura por estado.
4. La última acción también obtiene esta recaptura. Si no queda presupuesto de decisiones, el ciclo
   termina como `step_limit` únicamente después de la inspección local.
5. No se añade inferencia, persistencia, permiso, historial ni dependencia.

## Consecuencia

El límite de pasos deja de crear una excepción de seguridad. Todas las mutaciones remotas tienen una
observación posterior, mientras que la reutilización de esa observación evita duplicar trabajo o
aumentar la latencia de los pasos que continúan.
