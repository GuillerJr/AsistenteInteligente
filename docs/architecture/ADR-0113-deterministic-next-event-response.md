# ADR-0113: respuesta determinista del próximo evento

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. La consulta exacta de próximo evento conserva la llamada acotada de ADR-0110, pero elimina la
   fase de síntesis para resultados válidos, vacíos y fallidos.
2. El script local de Calendar añade `local_start_at`: un ISO 8601 con el offset del sistema
   aplicable a la fecha concreta del evento. `start_at` UTC continúa siendo la clave de orden global.
3. Jarvis valida que el timestamp sea consciente de zona y pertenezca a la ventana autorizada de
   31 días. El título se normaliza, se limita a 500 caracteres y debe ser imprimible.
4. Un evento produce una frase directa con título, fecha y hora; un título vacío conserva fecha y
   hora. Cero eventos y cualquier contrato inválido generan respuestas locales específicas.
5. Ninguna de estas rutas invoca planner, Apple Foundation Models ni NVIDIA.
6. No se añade herramienta, permiso, proceso, dependencia, persistencia ni consulta periódica.

## Motivo

Convertir un único evento estructurado en una frase no requiere inferencia. El formateo directo
reduce latencia, evita el fallback remoto y conserva la hora local correcta a través de cambios DST.

## Límites

- La fecha se presenta numéricamente como `DD/MM/AAAA` para no depender del locale del proceso.
- Solo se formatea la consulta exacta de un próximo evento; listar o resumir agendas conserva el
  flujo de síntesis acotado.
- Calendar solo se consulta después de una petición explícita y macOS mantiene el control TCC.
- Crear o modificar eventos conserva planificación, política y confirmación de un solo uso.
