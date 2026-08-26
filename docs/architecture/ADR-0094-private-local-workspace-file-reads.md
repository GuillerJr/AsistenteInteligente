# ADR-0094: lectura literal de archivos del workspace con cerebro local

- Estado: aceptado
- Fases: 1, 2, 4 y 5

## Decisión

1. La gramática exacta `Lee el archivo …` o `Read file …` construye localmente una llamada
   `filesystem_read_text` sin inferencia de planificación.
2. La ruta debe ser relativa al workspace y el camino rápido fija `max_bytes` en 8.192. Rutas
   absolutas, componentes `..` y solicitudes vacías no entran en este camino.
3. Se reutilizan el rol `CODE_SECURITY`, el broker, la política del workspace, la auditoría y el
   lector por descriptores que no sigue enlaces simbólicos.
4. El resultado se sintetiza con Apple Foundation Models on-device. Si el helper falla antes del
   primer delta, se conserva el fallback NVIDIA.
5. `Revisa el archivo …` y otras solicitudes de análisis permanecen en el especialista NVIDIA; el
   atajo solo resume una lectura literal y nunca sustituye razonamiento de código o seguridad.

## Motivo

La ruta y el límite ya determinan por completo la herramienta. Una ronda NVIDIA para reconstruir
esa llamada agrega latencia y expone el nombre del archivo sin mejorar la decisión. En 500 lecturas
simuladas con I/O real sobre un workspace temporal se observaron 500 síntesis locales, cero rondas
NVIDIA y 3,18 ms promedio de overhead interno. El proveedor local fue sustituido por un doble; la
medición no representa el tiempo real del modelo.

## Límites de seguridad

- El broker normaliza la ruta contra el workspace y deniega cualquier escape.
- El ejecutor abre cada componente con descriptores relativos y `O_NOFOLLOW`, exige un archivo
  regular y nunca invoca shell.
- Solo se decodifica UTF-8 y el fragmento directo no supera 8 KiB.
- El archivo es dato no confiable. Sus instrucciones no se obedecen y no pueden ampliar el único
  nombre de herramienta ofrecido por la solicitud.
- Si Apple Intelligence no está disponible, el fallback NVIDIA puede recibir el fragmento acotado.
  Una solicitud marcada para ejecución remota no entra en este atajo, pero tampoco puede ampliar la
  raíz permitida.
