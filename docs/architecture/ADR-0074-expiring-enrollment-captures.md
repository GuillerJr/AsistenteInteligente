# ADR-0074: Capturas de enrolamiento temporales y expirables

- Estado: aceptado
- Fases: 3 y 4

## Decisión

La palabra de activación y la identidad de hablante comparten un único contrato para sus archivos
en curso: `.pending-<UUID>.caf`, archivo regular del usuario actual, sin enlaces, modo `0600` y tamaño
máximo de 5 MiB. La captura asegura esos permisos inmediatamente después de crear el CAF.

Cada preparación del almacén revisa como máximo 16 temporales y elimina únicamente los que tengan
60 segundos o más. Una captura válida dura como máximo ocho segundos; el margen evita que otra
instancia de Jarvis elimine un archivo activo. Un nombre, tipo, propietario o permiso inesperado
bloquea el enrolamiento en lugar de ampliar el alcance de borrado.

## Consecuencias

- Un cierre forzado no conserva audio parcial indefinidamente.
- La limpieza no recorre subdirectorios ni toca muestras confirmadas o modelos.
- No se añade timer, daemon, dependencia ni tarea residente.
