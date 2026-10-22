# Revisión de ciclos dinámicos de Jarvis — 22 de octubre de 2026

Se contrastaron los criterios de identidad, cancelación y reanudación documentados
en [la tercera revisión REA](CODEX_REA_PARTS.md) y [la cuarta](CODEX_REA_FEATURES.md)
con el código y la ejecución controlada de Jarvis. Las pruebas usan gestores de
jobs reales, adaptadores sintéticos, relojes controlados y archivos temporales.
No se consultan historiales, credenciales, aplicaciones ni servicios del dueño.

Esta evidencia demuestra los escenarios declarados de **Jarvis**. No es una
observación de Codex en ejecución ni acredita el comportamiento de sus servidores.
No se copió código ni recursos del artefacto de referencia.

## Permisos permanentes y cierre de la aprobación

La ruta de «Aprobar siempre» para herramientas de lectura comprobaba el job bajo
el bloqueo del gestor, lo soltaba y guardaba el permiso mediante `to_thread`.
Solo después volvía a comprobar y consumir la aprobación. Se reprodujeron:

- Un cambio de política que rechaza la reautorización deja un permiso persistente.
- La caducidad al terminar la escritura rechaza la aprobación, pero conserva el permiso.
- Cancelar la tarea que espera la aprobación abandona el trabajador: este puede
  publicar el permiso después de que su solicitante ya haya terminado.
- Revocar un permiso existente mientras ese trabajador espera permite que la
  escritura antigua lo vuelva a crear.
- Un fallo de escritura anterior al reemplazo del archivo deja la aprobación
  disponible, aunque la operación «Siempre» haya fallado.

Las cinco situaciones se probaron tanto por la ruta local como por Telegram. La
prueba de Telegram invoca el adaptador interno con identidad sintética; no envía
mensajes reales ni aprueba acciones del dueño.

La escritura privada y acotada a 64 herramientas comparte ahora la misma sección
crítica que la reautorización, el consumo y el cambio de estado del job, siguiendo
el patrón existente de permisos por aplicación. Primero se comprueba que la
opción estaba disponible y que la herramienta sigue siendo de lectura. Una
reautorización rechazada no escribe el permiso; un fallo de guardado cierra el job
sin despachar la herramienta. La ruta no crea trabajadores tardíos de guardado ni
ejecuta una reversión que pueda deshacer una revocación posterior.

Las carreras admiten dos órdenes válidos: o la aprobación completa se acepta
antes del evento concurrente, o se rechaza sin publicar un permiso nuevo. Una
revocación posterior a la aprobación válida prevalece. Cancelar una espera después
de que una aprobación válida ya terminó no revoca por sí solo el permiso concedido.

Los errores de almacenamiento probados aquí suceden antes de reemplazar el
archivo. No se declara una transacción distribuida entre el sistema de archivos,
la auditoría y el job, ni se infiere que un fallo posterior a un reemplazo duradero
deshaga automáticamente los bytes que ya se guardaron.

## Ejecuciones desatendidas cerca del vencimiento

`run_unattended` podía pedir una espera de 0,01 a 0,099 segundos, mientras el gestor
real exige al menos 0,1 segundos. Un vencimiento situado a 50 ms reproducía el
rechazo del sondeo tanto durante ejecución como durante una aprobación pendiente.

Las esperas respetan ahora el mínimo de 100 ms; al volver se comprueba el plazo y
se cancela el job. Se permite que el último sondeo cruce ese plazo hasta el mínimo
del contrato; nunca se concede una aprobación por haber agotado el tiempo.

También se reprodujo que un error del callback de admisión, del aviso de aprobación
o del sondeo terminaba el supervisor sin cancelar el job ya admitido. Ahora esos
errores conservan la identidad del job, esperan su cancelación y después propagan
el fallo. Se registra solo el tipo de error. No se reenvía la solicitud ni se
repite una acción cuyo resultado sea incierto. Las pruebas retienen la cancelación
con una barrera y comprueban que el error no se entrega antes de terminarla.
Si se cancela mientras la admisión aún no devuelve el identificador, el supervisor
espera ese identificador y limpia el job incluso si después falla el callback que
lo registra. El resultado sigue siendo la cancelación original.

## Evidencia y pruebas

- Diez regresiones de permisos permanentes fallan con `jobs.py` de `4c6d4c7` y
  pasan con la corrección, incluidas cancelación del solicitante y revocación
  durante la escritura. La segunda reproducción carga el módulo histórico en un
  proceso aislado, sin reemplazar los archivos de trabajo.
- Seis regresiones desatendidas fallan antes de la corrección: dos usan el
  contrato de sondeo de `SwarmJobManager`, tres fallos controlados de observación y
  una combina admisión tardía, cancelación y fallo del callback de identidad.
- El contenido de error sintético privado no aparece en los logs.
- Las nuevas pruebas están en `tests/test_standing_grants.py` y
  `tests/test_unattended_lifecycle.py`; se contrastan además permisos por app,
  programación, cancelación, entrega y fronteras de excepción.

Los registros locales de reproducción se conservan temporalmente en
`/private/tmp/jarvis-dynamic-standing-before.log`,
`/private/tmp/jarvis-dynamic-owner-races-before.log` y
`/private/tmp/jarvis-dynamic-unattended-before.log`, más
`/private/tmp/jarvis-dynamic-admission-before.log`. Los controles completos de la
entrega se ejecutan aparte; las regresiones focales no sustituyen esos controles.
