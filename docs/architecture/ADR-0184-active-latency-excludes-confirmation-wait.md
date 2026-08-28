# ADR-0184: latencia activa separada de la confirmación humana

## Estado

Aceptada.

## Contexto

Una acción sensible puede permanecer en `awaiting_confirmation` mientras el propietario decide.
Incluir esa espera en la latencia de Jarvis confundía tiempo humano con procesamiento y podía marcar
como lenta una ejecución local que terminó en milisegundos. El tiempo de pared sigue siendo útil para
diagnosticar la experiencia completa y no debe perderse.

## Decisión

1. `total_latency_ms` conserva compatibilidad de contrato y pasa a representar tiempo activo de
   Jarvis: tiempo de pared menos los intervalos en espera de confirmación.
2. Cada evaluación nueva publica `wall_latency_ms` y `confirmation_wait_ms`; su suma se valida de
   forma cerrada: `total_latency_ms + confirmation_wait_ms == wall_latency_ms`.
3. El primer fragmento conserva la misma separación mediante `first_partial_latency_ms` activo y
   `wall_first_partial_latency_ms`. Ambos deben estar presentes juntos en evaluaciones nuevas.
4. La autoevaluación calcula sus compuertas de velocidad con latencia activa. También expone p95 de
   pared y confirmación para diagnóstico, sin convertir rapidez de aprobación en objetivo de IA.
5. Registros históricos sin los campos nuevos siguen siendo válidos; para agregarlos, su latencia de
   pared se interpreta como la latencia histórica existente y la espera de confirmación como cero.
6. Se usa el reloj monotónico inyectable. No se persisten texto, argumentos, identidad ni contenido
   de la confirmación.
7. Este cambio no omite, acorta ni automatiza confirmaciones. La autorización y su vencimiento
   conservan la política existente.

## Consecuencias

- Las métricas de rendimiento atribuyen a Jarvis únicamente el tiempo que controla.
- La experiencia de extremo a extremo continúa observable sin almacenar contenido personal.
- No se requiere migrar ni borrar el SQLite de evaluaciones existente.
- Clientes nativos rechazan relaciones de latencia inconsistentes antes de mostrarlas.
