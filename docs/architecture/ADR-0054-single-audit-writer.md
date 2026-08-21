# ADR-0054: Escritor único de auditoría

- Estado: aceptado
- Fase: 4 — Ciberseguridad
- Fecha: 2026-08-21

## Contexto

La cadena aceptaba crecimiento válido producido por otra instancia. Como los hashes no usan una
clave, cualquier proceso con acceso de escritura podía construir y anexar registros sintácticamente
válidos. La arquitectura desplegada tiene un solo daemon y no necesita escritores cooperativos.

También se evaluó persistir la punta en Keychain. Escribir Keychain en cada evento añadiría latencia,
otra credencial y un protocolo de bootstrap sin resolver por sí solo la eliminación conjunta del
registro y su ancla. Ese mecanismo se rechaza hasta definir una raíz durable completa.

## Decisión

La primera operación de una instancia establece su línea base. Desde entonces, una lectura o el
inicio de un append exige exactamente la misma longitud y punta observadas. Solo esa instancia puede
avanzar el ancla, después de escribir y sincronizar su propio registro.

El bloqueo POSIX se conserva para impedir corrupción por carreras, pero ya no concede autoridad de
escritura a procesos adicionales.

## Filtro del algoritmo de ingeniería

1. Se cuestionó el supuesto de que varios escritores fueran un requisito.
2. Se eliminó la tolerancia a escritores externos y se descartó un ancla incompleta en Keychain.
3. Se reutilizan la longitud, punta y bloqueo ya presentes.
4. No se agregan I/O, procesos ni dependencias al camino crítico.
5. El monitor existente convierte automáticamente el crecimiento externo en `compromised`.

## Consecuencias

Jarvis detecta anexos externos mientras el daemon permanece activo. Una nueva instancia establece
su línea base con la cadena existente; autenticar continuidad entre reinicios sigue fuera de este
incremento.
