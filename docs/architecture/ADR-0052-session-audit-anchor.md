# ADR-0052: Ancla de auditoría durante la sesión

- Estado: aceptado
- Fase: 4 — Ciberseguridad
- Fecha: 2026-08-21

## Contexto

La cadena SHA-256 detecta la edición de registros, pero una cadena válida completa podía
eliminarse, sustituirse o truncarse. Un archivo nuevo con permisos `0600` parecía una
auditoría vacía o íntegra.

## Decisión

Cada instancia activa de `HashChainAuditLog` conserva únicamente en memoria:

- la identidad POSIX del archivo (`st_dev`, `st_ino`);
- la cantidad de registros ya observados;
- el hash de la punta observada.

Cada lectura y escritura, dentro del bloqueo del archivo, exige la misma identidad y que
la cadena actual contenga la punta anclada. La desaparición, sustitución, truncado o
reescritura histórica falla de forma cerrada y el monitor informa `compromised`.

## Filtro del algoritmo de ingeniería

1. Se cuestionó que una cadena hash por sí sola probara continuidad temporal.
2. Se descartaron Keychain, base de datos y archivos laterales: no son necesarios para
   detectar rollback durante la vida del daemon.
3. Se reutilizan `fstat`, el bloqueo existente y tres valores escalares.
4. La verificación continúa siendo síncrona y local, sin I/O adicional.
5. El monitor de seguridad aplica la comprobación automáticamente en cada consulta.

## Consecuencias

Jarvis detecta rollback durante la sesión activa sin nuevas dependencias ni secretos. Un
reinicio establece una ancla nueva; proteger continuidad entre reinicios requiere una
raíz durable autenticada y queda fuera de este incremento.
