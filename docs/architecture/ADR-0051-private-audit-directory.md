# ADR-0051: Directorio privado del registro de auditoría

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** proteger solo `audit.jsonl` no protege su identidad si el directorio contenedor es
   sustituible, compartido o un enlace simbólico.
2. **Eliminar:** no se incorpora base de datos, servicio de logs, ACL propia ni rotación.
3. **Simplificar:** se replica el contrato owner-only que ya usa la memoria SQLite local.
4. **Acelerar:** una única validación sirve para append y `security.status`.
5. **Automatizar:** cualquier degradación del directorio produce el estado agregado `compromised`.

## Decisión

Antes de crear o abrir el log, `HashChainAuditLog` usa `lstat` sobre el directorio final y exige que
sea un directorio real, propiedad del UID del proceso y sin bits para grupo/otros. Cuando todavía no
existe, solo la ruta de escritura lo crea con `0700`; una verificación de un log inexistente continúa
siendo vacía y no muta el disco.

El archivo abierto mediante `O_NOFOLLOW` conserva sus controles de tipo y `0600`, y ahora también
exige el UID esperado mediante `fstat`. `verify()` revalida el directorio en cada consulta, de modo
que el monitor pasivo detecta un cambio posterior de permisos.

## Encaje en el roadmap

- **Fase 4 — Ciberseguridad:** cierra redirección y exposición del registro append-only.
- **Fase 5 — Voice-first:** convierte la degradación en el bloqueo agregado que ya observa Menu Bar.

## Consecuencia

Una instalación heredada con permisos amplios falla cerrada hasta que el operador restaure `0700` en
el directorio. No se corrigen permisos automáticamente porque hacerlo ocultaría una alteración de la
frontera de confianza.
