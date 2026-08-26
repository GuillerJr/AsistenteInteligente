# ADR-0097: respuestas deterministas para lecturas vacías

- Estado: aceptado
- Fases: 1, 2, 4 y 5

## Decisión

1. Después de una lectura exitosa apta para síntesis local, el grafo valida primero si el resultado
   está vacío según el contrato exacto de su herramienta.
2. Mail exige únicamente `{"messages":[]}`; Calendario `{"events":[]}`; investigación web exige
   query no vacío y `results` vacío; una página exige URL no vacía en el resultado y `content` vacío.
   Un archivo exige salida vacía, cero bytes leídos y ausencia de truncamiento.
3. Un resultado vacío válido produce una frase española fija, se publica como primer fragmento y
   termina con modelo atribuido `local/deterministic-empty-read`.
4. No se invoca Apple Foundation Models ni NVIDIA para esa síntesis. Una lectura abierta que necesitó
   planificación NVIDIA conserva únicamente esa primera ronda.
5. Salidas fallidas, malformadas, con claves adicionales, no vacías o fuera de los roles permitidos
   conservan el synthesizer existente.

## Motivo

Un modelo no aporta razonamiento al afirmar que una colección validada contiene cero elementos.
Eliminar esa inferencia reduce tiempo al primer fragmento, consumo y superficie de datos sin cambiar
autoridad. En 500 lecturas exactas simuladas se observaron cero rondas Apple, cero rondas NVIDIA y
2,23 ms promedio de overhead interno. El ejecutor fue sustituido por un doble; no se midió Mail, web
ni almacenamiento reales.

## Límites

- El broker, ejecutor y auditoría siempre terminan antes de evaluar si el resultado está vacío.
- Un error de acceso, timeout o contrato inválido nunca se presenta como ausencia de datos.
- La misma compuerta de roles que protege la síntesis local evita degradar análisis de
  código/ciberseguridad.
- Las frases fijas no incluyen argumentos, rutas, query ni otros datos potencialmente sensibles.
- No se añaden herramientas, permisos, dependencias ni estado persistente.
