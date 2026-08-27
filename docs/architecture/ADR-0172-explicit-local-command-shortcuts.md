# ADR-0172: atajos Command locales explícitos

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Evidencia

Python y el helper firmado ya permitían únicamente `⌘A`, `⌘F`, `⌘L`, `⌘R` y `⌘T`, pero una orden
inequívoca todavía requería que visión devolviera la combinación conocida. Eso añadía una inferencia
y exponía una captura sin aportar una decisión real.

## Decisión

1. Frases completas y acotadas representan seleccionar todo, buscar en la página, enfocar la barra
   de direcciones, recargar la página y abrir una pestaña nueva, además de equivalentes directos en
   inglés.
2. Cada frase produce exclusivamente una acción `key` con el modificador `command` y la tecla
   `a/f/l/r/t` correspondiente. No se añade otra combinación al decoder o al helper.
3. La coincidencia exige la orden completa con puntuación final opcional. Texto adicional o una
   composición de dos acciones no ejecuta un prefijo local.
4. `secure_content` bloquea antes del fast-path. La aprobación de un solo uso, bundle ID, PID,
   contexto visual y entrega directa al proceso permanecen obligatorios.
5. La captura y la orden no se envían a NVIDIA. ADR-0170 verifica después progreso semántico o visual
   y bloquea contenido sensible revelado por el atajo.
6. Enter, Espacio, cierre, envío, eliminación y cualquier atajo fuera de `⌘A/F/L/R/T` continúan
   rechazados por Python y Swift.

## Consecuencia

Las operaciones de navegación y edición no destructiva evitan una llamada de visión sin ampliar la
autoridad del sistema. Las frases ambiguas conservan la ruta remota protegida y nunca producen una
ejecución parcial silenciosa.
