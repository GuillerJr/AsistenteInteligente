# ADR-0173: secuencia local verificada de búsqueda en página

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Evidencia

ADR-0172 permite abrir el buscador de página con `⌘F` y ADR-0171 permite escribir un literal local,
pero pedir ambas operaciones obligaba a visión a coordinarlas. Ejecutarlas juntas sin comprobar el
primer efecto podría escribir el texto en el campo que estaba enfocado antes de abrir el buscador.

## Decisión

1. Solo `Busca «…» en la página`, `Buscar «…» en la página` y `Find "…" on page` con delimitadores
   cerrados producen la secuencia local.
2. El controlador reserva dos pasos disponibles antes de ejecutar nada. Sin ese presupuesto conserva
   la ruta remota y no ejecuta un prefijo parcial.
3. La primera acción es exclusivamente `⌘F`. Jarvis recaptura y exige progreso semántico o visual;
   contenido sensible o ausencia de cambio termina la sesión antes de escribir.
4. La segunda acción escribe el literal de 1 a 500 caracteres en el campo que el helper valida como
   enfocado, editable, no seguro y perteneciente al mismo proceso. No usa portapapeles ni Enter.
5. Cada acción obtiene su propio contexto visual y verificación postacción. Un contexto caducado usa
   como máximo la recuperación local de ADR-0168 y conserva acción y literal exactos.
6. La captura, el literal y las observaciones no se envían a NVIDIA ni se persisten. La aprobación de
   control visual de un solo uso continúa cubriendo el objetivo completo y los dos efectos.

## Consecuencia

La búsqueda dentro de una página tarda dos acciones locales y ninguna inferencia. El texto nunca se
entrega si el buscador no aparece de forma verificable, evitando que un fallo de foco convierta una
orden de búsqueda en escritura sobre otro campo.
