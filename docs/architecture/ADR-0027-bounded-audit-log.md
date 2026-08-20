# ADR-0027: Registro de auditoría acotado y fail-closed

- Estado: aceptado
- Fecha: 2026-08-20

## Filtro de ingeniería

1. **Cuestionar:** un registro append-only ilimitado convierte la auditoría en un riesgo de memoria y disco.
2. **Eliminar:** no se añade un servicio de rotación, compresión ni una segunda base de datos.
3. **Simplificar:** se impone una sola cota en bytes, comprobada bajo el mismo bloqueo del append.
4. **Acelerar:** el límite predeterminado funciona sin configuración ni migración.
5. **Automatizar:** verificación y escritura fallan de forma segura cuando el archivo supera la cota.

## Decisión

El registro `audit.jsonl` se limita a 16 MiB por defecto. `AEGIS_AUDIT_MAX_BYTES` permite configurar
entre 64 KiB y 256 MiB. Antes de leer o escribir se valida el tamaño mediante el descriptor abierto;
antes del append también se comprueba el tamaño exacto del registro serializado bajo el bloqueo
exclusivo.

No se trunca ni rota automáticamente. Al alcanzar la capacidad, las nuevas operaciones auditadas
fallan antes de ejecutarse y `security.status` informa `compromised` si el archivo ya supera el límite.

## Consecuencias

El daemon mantiene consumo de memoria y disco previsible. Archivar y reiniciar la cadena requerirá
en el futuro un procedimiento explícito que preserve la cabeza de confianza; no se oculta esa
transición mediante borrado automático.
