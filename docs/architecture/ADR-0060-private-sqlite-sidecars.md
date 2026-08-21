# ADR-0060: Sidecars SQLite privados

- Estado: aceptado
- Fase: 2 — Memoria persistente
- Fecha: 2026-08-21

## Contexto

SQLite puede leer o crear un rollback journal, WAL y memoria compartida junto a la base. Jarvis
aseguraba `-wal` y `-shm` después de ciertas escrituras, pero no validaba esos archivos antes de
conectar y omitía `-journal`. Un sidecar inseguro podía alcanzar SQLite antes de la comprobación.

## Decisión

Antes y después de cada `sqlite3.connect` se validan `memory.sqlite3-journal`, `-wal` y `-shm`. Si
existen, deben ser archivos regulares del UID esperado y negar acceso a grupo y otros.

Después de operaciones mutables, la base y los sidecars existentes se abren con `O_NOFOLLOW`, se
validan mediante `fstat` y reciben modo `0600` con `fchmod`. La base principal debe conservar además
su identidad anclada.

## Filtro del algoritmo de ingeniería

1. Se cuestionó que proteger el archivo `.sqlite3` cubriera todos los archivos usados por SQLite.
2. Se descartaron WAL permanente, watcher y librería criptográfica adicional.
3. Se amplía la validación POSIX existente a tres nombres deterministas.
4. Solo se consultan metadatos locales; no hay hashing ni copias.
5. `_connect` y el cierre de escrituras aplican la política automáticamente.

## Consecuencias

Un sidecar simbólico, ajeno o público bloquea la memoria antes de ejecutar consultas. La política
reduce sustituciones de ruta y exposición POSIX, pero la confidencialidad en reposo sigue dependiendo
de FileVault y de la sesión del usuario.
