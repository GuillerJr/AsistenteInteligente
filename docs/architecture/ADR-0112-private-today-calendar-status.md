# ADR-0112: estado privado del calendario de hoy

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. Una gramática exacta convierte «¿Tengo eventos hoy?», «¿Tengo algo en el calendario hoy?» y
   equivalentes cerrados en `calendar_list_events` con `limit=1`.
2. La ventana comienza y termina en las medianoches locales consecutivas del día actual. Cada borde
   conserva su offset para tolerar días de 23 o 25 horas.
3. Un resultado produce «Tienes al menos un evento hoy»; cero resultados produce «No tienes eventos
   hoy». Título, calendario, ubicación y horario no se interpolan en la respuesta.
4. Esta ruta no invoca planner, Apple Foundation Models ni NVIDIA. Un fallo desconocido, cantidad
   imposible o estructura inválida también termina en una respuesta determinista local.
5. El contrato distingue una ventana diaria por sus dos medianoches y fechas consecutivas. El
   mensaje de «próximo evento» exige por separado una ventana exacta de 31 días reales.
6. Consultas de cantidad, órdenes compuestas o fechas ambiguas conservan el cerebro normal.
7. No se añade herramienta, permiso, proceso, dependencia, persistencia ni consulta periódica.

## Motivo

Comprobar si el día contiene algún evento es una operación binaria local. Reutilizar Calendar con
un solo resultado reduce latencia, evita inferencia innecesaria y mantiene privados sus metadatos.

## Límites

- La respuesta afirma existencia, no cuenta ni resume eventos.
- Calendar solo se consulta después de una petición explícita; no es un monitor.
- macOS conserva la decisión TCC sobre Calendar.
- Listar la agenda continúa usando el flujo acotado de ADR-0092.
- Crear o modificar eventos conserva planificación, política y confirmación de un solo uso.
