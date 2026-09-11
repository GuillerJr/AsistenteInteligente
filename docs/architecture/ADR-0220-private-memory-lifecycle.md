# ADR-0220: Ciclo de vida de memoria privada

- Estado: aceptado
- Fecha: 2026-09-11
- Bloque: pulido de memoria

## Decisión

El esquema v9 extiende el cifrado autenticado a las conversaciones mediante un codec separado,
reutilizando la clave y el cifrador existentes con dominios AAD diferentes. La cabecera autentica
el total de turnos; cada turno autentica identidad, conversación, rol, orden y metadatos. El append
mantiene ambos turnos y la cabecera en una única transacción. La migración v8 reconstruye las tablas
con cascada, conserva sus datos y revierte íntegramente ante fallos; la clave se valida primero.

Caducidad significa comparar instantes, no cadenas ISO. La misma función se usa para búsqueda y
expulsión; el grafo proyecta únicamente procedencia vigente en una instantánea por lectura. Los
atributos físicos compartidos requieren evidencia viva, aunque el nodo tenga otros vecinos. La
limpieza de mantenimiento no constituye una condición para dejar de recuperar un recuerdo vencido.

Las operaciones SQLite mutables de conversación y del servicio GraphRAG retienen su worker hasta
terminar. Los bloqueos de conversación se liberan de su registro cuando no quedan usuarios ni
esperadores. El caché PPR tiene capacidad fija y un cambio de época impide repoblarlo con búsquedas
anteriores al olvido. La reconstrucción del paquete conserva el flujo SwiftPM/macOS existente.

## Consecuencias y prueba

La confidencialidad del contenido no oculta todos los metadatos del archivo, no autentica de forma
retroactiva el pasado en texto plano y no proporciona protección contra rollback de una copia
completa auténtica. No se promete borrado físico de SSD ni de copias externas. Un binario pre-v9
rechaza la nueva base; no debe forzarse `user_version` para intentar abrirla.

El [informe de memoria](../quality/MEMORY_POLISH.md) detalla reproducciones, límites y comandos.
Se mantienen los filtros de secretos, el aislamiento por namespace, la memoria local y la política
NVIDIA del CLI. No se activan Spotlight, permisos macOS ni servicios remotos como efecto secundario.
