# ADR-0095: síntesis local después de lecturas planificadas

- Estado: aceptado
- Fases: 1, 2, 4 y 5

## Decisión

1. Una lectura exitosa `mail_list_recent`, `calendar_list_events`, `web_fetch` o `web_research`
   solicitada por el rol `PLANNER` intenta sintetizarse con Apple Foundation Models on-device.
2. NVIDIA conserva la primera ronda cuando la solicitud no coincide con una gramática determinista
   y debe seleccionar o parametrizar la herramienta.
3. El helper de Apple se invoca únicamente después de autorización, ejecución y auditoría. No recibe
   esquemas, no propone llamadas y no adquiere capacidades.
4. Si Apple falla antes del primer delta, el synthesizer NVIDIA conserva el fallback existente. Un
   fallo posterior a un delta no concatena otra respuesta.
5. Lecturas del rol `CODE_SECURITY` y archivos no deterministas permanecen en NVIDIA para conservar
   razonamiento especializado. La lectura literal exacta de archivo mantiene su excepción local ya
   definida en ADR-0094.

## Motivo

La primera ronda remota puede ser necesaria para comprender una solicitud abierta; la segunda solo
combina un resultado acotado ya obtenido. Usar el cerebro local para esa combinación reduce latencia
y evita enviar a NVIDIA resultados privados de Mail o Calendario en el caso normal. En 500
ejecuciones simuladas se observaron 500 rondas NVIDIA de planificación, 500 síntesis locales, cero
síntesis NVIDIA y 3,80 ms promedio de overhead interno. Proveedores y ejecutor fueron dobles; la
medición no representa latencia real de red, aplicaciones o modelos.

## Límites

- La política, confirmaciones, argumentos y auditoría no cambian.
- El resultado de herramienta continúa siendo dato no confiable y no puede ampliar autoridad.
- Apple recibe solo el contexto acotado del synthesizer y nunca credenciales ni cuerpos de correo.
- Si el helper local está ausente, excede su límite o falla antes de responder, el fallback NVIDIA
  puede recibir el resultado acotado.
- Investigaciones de seguridad y análisis de código conservan el synthesizer remoto aunque utilicen
  una lectura web.
