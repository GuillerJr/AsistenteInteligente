# ADR-0002: IPC local autenticado

- Estado: aceptado
- Fecha: 2026-08-18

## Contexto

El daemon Python, la futura aplicación de menú y el HUD Tauri serán procesos distintos. Una ruta de
socket privada reduce la superficie frente a un puerto TCP, pero sus permisos por sí solos no
autentican cada mensaje ni protegen contra replay.

## Decisión

1. La comunicación local utilizará un Unix Domain Socket dentro de un directorio `0700`; el socket
   tendrá permisos `0600` y pertenecerá al usuario efectivo del daemon.
2. En macOS, el daemon validará el UID efectivo del cliente con `LOCAL_PEERCRED`. Otros UID serán
   desconectados antes de leer o procesar solicitudes.
3. Solicitudes y respuestas usarán envelopes JSON del protocolo `1.0`, autenticados con
   HMAC-SHA256. El secreto de 256 bits se generará una vez y permanecerá en macOS Keychain.
4. Cada solicitud incluirá UUID, timestamp y nonce aleatorio de 128 bits. La ventana temporal será
   de 30 segundos y la caché de nonces fallará cerrada al alcanzar su capacidad.
5. Se procesará una única solicitud por conexión, con timeout, límite predeterminado de 64 KiB y un
   máximo acotado de clientes concurrentes.
6. El daemon solo eliminará sockets obsoletos que sean realmente sockets y pertenezcan al usuario
   efectivo. Al cerrar, eliminará únicamente el inode que él mismo creó.
7. El control plane expondrá `health`, `runtime.info` y operaciones asíncronas de jobs. Ninguna
   operación directa de red, terminal o herramientas mutables estará disponible por IPC.

## Consecuencias

- El HUD y la aplicación de menú tendrán una frontera local pequeña y versionada.
- Conocer la ruta del socket no basta para falsificar solicitudes o respuestas.
- Los frames capturados no pueden reutilizarse dentro o fuera de la ventana de validez.
- La rotación futura del secreto requerirá coordinación entre daemon y clientes, pero no afecta la
  credencial NVIDIA.
