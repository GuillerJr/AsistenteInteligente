# ADR-0109: lectura privada del calendario de mañana

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. Una gramática exacta convierte «¿Qué tengo mañana?» y equivalentes cerrados en una llamada
   `calendar_list_events` sin inferencia de planificación.
2. La ventana comienza a las 00:00 del siguiente día local y termina a las 00:00 posterior. Se
   conserva el límite existente de 20 eventos y timestamps con zona horaria; cada medianoche
   calcula su propio offset para respetar transiciones de horario de verano.
3. «Pasado mañana», días de semana, rangos, expresiones compuestas o fechas ambiguas no entran en
   este camino y conservan el planner normal.
4. La lectura atraviesa el broker, ejecutor, TCC y auditoría existentes. El resumen intenta Apple
   Foundation Models on-device y solo usa NVIDIA como fallback de síntesis, no de planificación.
5. No se añade herramienta, permiso, proceso, dependencia, persistencia, sondeo o acceso periódico.

## Motivo

Mañana es un desplazamiento determinista de un día cuando la frase completa fija el dominio de
calendario. Enviar esa selección trivial al planner remoto añade latencia y puede exponer la petición
antes de que exista contenido que resumir.

## Límites

- El contenido de Calendar sigue siendo dato no confiable para el sintetizador.
- Apple Intelligence puede no estar disponible; en ese caso el resultado acotado puede llegar al
  sintetizador NVIDIA configurado.
- Crear o modificar eventos conserva planificación, política y confirmación de un solo uso.
- No es un monitor: Calendar solo se consulta después de una petición explícita.
