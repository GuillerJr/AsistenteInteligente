# ADR-0092: lecturas privadas de Mail y Calendario con cerebro local

- Estado: aceptado
- Fases: 1, 2, 3 y 5

## Decisión

1. Una gramática exacta convierte solicitudes de lectura de inbox y calendario de hoy en
   `mail_list_recent` o `calendar_list_events` sin una inferencia de planificación.
2. Mail queda limitado a 10 mensajes y puede filtrar no leídos. Conserva el contrato existente de
   metadatos y nunca solicita cuerpos.
3. Calendario queda limitado a 20 eventos dentro del día actual calculado con la zona horaria local.
   ADR-0109 extiende únicamente el siguiente día exacto; otros rangos relativos o intervalos
   ambiguos no entran en este camino.
4. La llamada pasa por el mismo broker y ejecutor de solo lectura. Después, el rol synthesizer usa
   Apple Foundation Models on-device, que ahora admite explícitamente `PLANNER` y `SYNTHESIZER`.
5. Si el helper local falla antes del primer delta, el cortacircuito y fallback NVIDIA existentes
   completan el resumen. Un fallo posterior a un delta no concatena otra respuesta.

## Motivo

Para «Revisa mi correo», la primera llamada NVIDIA solo seleccionaba una función sin argumentos
inciertos y una segunda llamada resumía el resultado. La planificación local elimina esa ronda. La
síntesis on-device reduce latencia de red y mantiene metadatos personales en el Mac durante el caso
normal. En 500 ejecuciones simuladas del grafo se observaron cero rondas NVIDIA y 2,08 ms promedio de
overhead interno, sin medir el tiempo real de las aplicaciones ni del modelo.

## Límites

- Es una lectura explícita, no escucha ni consulta periódica en segundo plano.
- macOS conserva la decisión TCC sobre Mail y Calendario.
- El fallback NVIDIA puede recibir el resultado acotado si Apple Intelligence no está disponible.
- Crear eventos, enviar correo y cualquier mutación conservan planificación, política y confirmación
  de un solo uso.
- El contenido devuelto por las aplicaciones sigue tratándose como dato no confiable durante la
  síntesis.
