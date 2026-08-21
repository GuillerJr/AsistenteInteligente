# ADR-0059: Directorio de memoria anclado

- Estado: aceptado
- Fase: 2 — Memoria persistente
- Fecha: 2026-08-21

## Contexto

SQLite revalidaba el inode, propietario y permisos de `memory.sqlite3` en cada conexión, pero el
directorio contenedor `0700` solo se comprobaba durante `initialize`. Cambiarlo a un modo público o
sustituirlo durante la sesión no detenía necesariamente las operaciones posteriores.

## Decisión

La inicialización conserva `(st_dev, st_ino)` del directorio. Antes y después de abrir cada conexión
se exige que siga siendo un directorio real, con el mismo inode, propietario esperado y sin permisos
para grupo u otros. La misma validación precede el aseguramiento de la base y sus sidecars.

La comprobación posterior a `sqlite3.connect` reduce la ventana entre validar la ruta y abrirla; si
la identidad cambió, no se ejecutan consultas de aplicación mediante esa conexión.

## Filtro del algoritmo de ingeniería

1. Se cuestionó que proteger solo el archivo principal protegiera su ruta y sidecars.
2. Se descartaron sandbox adicional, watcher y proceso de monitoreo.
3. Se reutilizan `lstat`, UID, modo e identidad POSIX ya usados para la base.
4. Cada conexión añade dos lecturas locales de metadatos, sin red ni hashing de contenido.
5. Todas las operaciones heredan la comprobación desde `_connect`.

## Consecuencias

Ampliar permisos, eliminar o sustituir el directorio de memoria detiene lecturas y escrituras durante
la sesión. La comprobación protege identidad y acceso POSIX, no cifra el contenido de SQLite.
