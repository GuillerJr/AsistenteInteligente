# ADR-0098: explicación determinista de errores de lectura conocidos

- Estado: aceptado
- Fases: 1, 4 y 5

## Decisión

1. Una lectura apta para síntesis local que termina con un único resultado fallido y salida vacía
   puede producir una respuesta determinista solo para códigos conocidos.
2. Se admiten `file_not_found`, `invalid_utf8`, `access_denied`, `execution_timeout`,
   `web_access_failed` e `io_error`. Los dos primeros y el fallo web se restringen además a las
   herramientas donde tienen significado preciso.
3. La frase no contiene argumentos, rutas, URLs, query, salida de herramienta ni detalles internos.
4. El texto se publica como primer fragmento y resultado final bajo
   `local/deterministic-read-error`, sin invocar Apple Foundation Models o NVIDIA para sintetizarlo.
5. Un error desconocido, resultado múltiple, salida no vacía, rol no elegible o herramienta distinta
   conserva el synthesizer existente.

## Motivo

El ejecutor ya clasificó el fallo en una frontera local y auditable. Pedir a un modelo que reformule
`file_not_found` añade latencia y puede inventar causas. En 500 ejecuciones simuladas se observaron
cero rondas Apple, cero rondas NVIDIA y 2,36 ms promedio de overhead interno. El fallo del ejecutor
fue simulado para aislar la orquestación.

## Límites

- Broker, autorización, ejecutor y auditoría preceden siempre a la respuesta determinista.
- Una denegación de política no llega a esta ruta porque no se ejecuta como lectura autorizada.
- `access_denied` describe únicamente la negativa observada durante ejecución; no identifica ni
  modifica permisos TCC.
- No se reintenta automáticamente, no se amplía alcance y no se abre Ajustes del Sistema.
- Investigaciones de código/ciberseguridad y códigos nuevos conservan análisis remoto.
