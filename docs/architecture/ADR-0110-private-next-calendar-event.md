# ADR-0110: próximo evento privado y determinista

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. Una gramática exacta convierte «¿Cuál es mi próximo evento?», «¿Qué sigue en mi calendario?» y
   equivalentes cerrados en una llamada `calendar_list_events` sin inferencia de planificación.
2. La ventana comienza en el instante local de la petición, excluye microsegundos y termina como
   máximo 31 días reales después. Solicita un único evento.
3. Calendar reúne candidatos de todos los calendarios, impone un tope duro de 2.048, ordena
   globalmente por inicio y solo entonces aplica el límite. Superar el tope o recibir una estructura
   inválida produce un fallo cerrado.
4. Solicitudes plurales, compuestas, relativas a un día o ambiguas conservan el planner normal.
5. La lectura atraviesa el broker, ejecutor, TCC y auditoría existentes. ADR-0113 sustituye la
   síntesis por un formateador local determinista que no invoca Apple Foundation Models ni NVIDIA.
6. No se añade herramienta, permiso, proceso, dependencia, persistencia ni sondeo periódico.

## Motivo

Elegir el evento futuro cronológicamente más cercano es una operación local determinista. Omitir el
planner remoto reduce latencia y evita exponer la intención; ordenar antes de limitar evita que el
orden interno de los calendarios seleccione un evento posterior.

## Límites

- Un evento situado a más de 31 días no se devuelve y Jarvis declara ese horizonte en la respuesta
  vacía.
- El contenido de Calendar sigue siendo dato no confiable para el formateador local.
- Título, fecha y hora se presentan localmente; un contrato inválido falla cerrado sin sintetizador.
- Crear o modificar eventos conserva planificación, política y confirmación de un solo uso.
- No es un monitor: Calendar solo se consulta después de una petición explícita.
