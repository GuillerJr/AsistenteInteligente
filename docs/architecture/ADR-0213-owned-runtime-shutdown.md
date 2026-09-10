# ADR-0213: cierre completo del núcleo antes del sello de auditoría

- Estado: aceptado
- Responsable: Guillermo (`gzambrano27`)
- Alcance: pulido del punto 1, núcleo y ejecución; sin capacidades nuevas

El cierre anterior retiraba el listener UDS sin ser dueño de sus handlers activos. En Python 3.11
podían sobrevivir al cierre; las versiones que esperan conexiones en `wait_closed()` podían quedar
bloqueadas. Dos cierres simultáneos de workers tampoco esperaban la misma limpieza. En Swift,
`stop()` soltaba el proceso después de SIGTERM, antes de que terminara.

El servidor reserva capacidad en el callback síncrono de aceptación, conserva cada tarea y socket,
y rechaza conexiones cuando empieza a cerrar. Cancela y drena sus clientes antes de retirar su
socket. El listener solo se activa después de validar permisos; un error revierte el arranque.
El cierre del IPC, jobs y workers es compartido e inmune a la cancelación de un solo solicitante.
Cancelar un job repetidamente no vuelve a interrumpir su sección de limpieza.

El apagado usa `AsyncExitStack` para detener productores antes que dependencias y ejecutar todos
los cierres aunque uno falle. Une el executor de hilos antes de sellar la auditoría: cancelar
`to_thread()` no detiene el trabajo subyacente. El sello solo se escribe si termina la limpieza.

El supervisor Swift conserva el hijo hasta su callback de terminación. Un arranque durante ese
intervalo falla con `shutdownInProgress`. AppKit aplaza la salida y responde cuando termina el
daemon, sin bloquear el actor principal. Tras diez segundos, solo el hijo aún vivo y perteneciente
a esa generación recibe SIGKILL. No se fabrica un sello limpio para ese caso; la validación de
arranque conserva su comportamiento de fallo cerrado.

No se añaden sondeos en reposo, dependencias, procesos permanentes ni telemetría remota. Las pruebas
usan sockets temporales y procesos desechables; la voz del propietario sigue fuera de alcance.
