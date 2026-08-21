# ADR-0053: Confianza de auditoría enclavada por sesión

- Estado: aceptado
- Fase: 4 — Ciberseguridad
- Fecha: 2026-08-21

## Contexto

El monitor detectaba una alteración, pero una restauración posterior del archivo podía hacer que
otra consulta devolviera `intact`. Esto convertía un incidente observado en un estado transitorio.

## Decisión

`HashChainAuditLog` revoca en memoria la confianza de la sesión ante cualquier fallo de integridad
o de I/O. Las verificaciones y escrituras posteriores fallan cerradas, aunque el archivo vuelva a
parecer válido. La capacidad agotada no revoca confianza porque el append se rechaza antes de
modificar el registro y su cadena sigue siendo verificable.

Un `RLock` local serializa el estado de sesión con cada operación. El bloqueo POSIX existente sigue
coordinando instancias y procesos distintos.

## Filtro del algoritmo de ingeniería

1. Se rechazó el requisito implícito de que la integridad pudiera recuperarse automáticamente.
2. Se descartaron un servicio, una base y un archivo de estado adicionales.
3. La solución usa un booleano y un bloqueo nativo alrededor del I/O ya existente.
4. La comprobación no añade llamadas externas ni nuevas lecturas de disco.
5. El monitor y todas las escrituras heredan el cierre seguro automáticamente.

## Consecuencias

Un incidente observado permanece `compromised` hasta reiniciar el daemon. El enclavamiento no
persiste entre reinicios; la continuidad durable sigue siendo una decisión separada.
