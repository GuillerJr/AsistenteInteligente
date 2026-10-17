# Seguridad de Jarvis

## Versiones admitidas

Hasta la publicación de la primera versión estable, únicamente la revisión más reciente de `main`
recibe correcciones de seguridad. Los ZIP locales, builds ad hoc y commits anteriores no se
consideran distribuciones soportadas.

## Reportar una vulnerabilidad

No abras un issue público si el reporte incluye una vulnerabilidad explotable, datos personales,
capturas, audio, credenciales o rutas privadas. Utiliza el formulario privado **Report a
vulnerability** de GitHub en la sección *Security Advisories* del repositorio. Incluye solamente:

- revisión Git completa y versión de macOS;
- frontera afectada (`IPC`, memoria, Keychain, TCC, audio, visión, herramientas o distribución);
- impacto observable y pasos mínimos para reproducirlo;
- prueba sin secretos ni contenido personal;
- propuesta de mitigación, si existe.

El mantenedor confirmará recepción antes de solicitar evidencia adicional. No se promete un plazo
de respuesta contractual mientras el proyecto permanezca en fase previa a una versión estable.

## Alcance defensivo

Son especialmente relevantes el bypass de HMAC o autorización, escape del workspace, confusión de
PID/bundle, replay de voz, exfiltración hacia proveedores, manipulación de SQLite/audit log,
ZIP-slip, carga de helpers no firmados y ejecución de herramientas fuera de su schema.

No se aceptan pruebas que degraden servicios de terceros, capturen credenciales ajenas, persistan
en otros equipos o eludan TCC fuera de un Mac expresamente autorizado por su propietario.

## Fronteras de autorización

- Una aprobación pendiente dura como máximo dos minutos de tiempo monotónico; adelantar el reloj
  puede acortarla, pero atrasarlo no la prolonga. Se comprueba el job antes y después de verificar
  voz, de modo que una cancelación o caducidad durante la transcripción no ejecuta la acción.
- El consentimiento liga el identificador, herramienta, digest y argumentos normalizados de la
  llamada retenida. Reautorizar no permite cambiar el destino o los valores revisados. Una
  denegación, excepción o discrepancia invalida cualquier grant emitido que no se haya consumido.
- Los grants son de un solo uso y su almacén está limitado a 4096 entradas. Al llenarse solo retira
  entradas caducadas; no descarta protecciones activas de consumo o revocación. El almacén de grants
  y la ventana de nonces IPC rechazan retrocesos respecto al último reloj de pared observado.
- La voz exige propietario verificado, probabilidad finita entre 0 y 1 y un operador afirmativo
  acotado. Preguntas, citas, negaciones y respuestas ambiguas no conceden autoridad. Booleanos y
  cadenas no se convierten en probabilidades ni en evidencia de identidad válida.
- La protección de replay vocal conserva hasta 512 hashes de PCM durante quince minutos en RAM.
  Los invitados no ocupan esa reserva y, si se llena, la vía vocal falla cerrada hasta liberar
  entradas caducadas. Cancelar la transcripción no libera el hash. No se registran audio, hash ni
  transcripción en la auditoría de aprobación.
- El daemon comparte su estado de integridad con broker, ejecución aprobada y planificador. Si
  queda comprometido, se rechazan nuevas entradas a herramientas y pasos de recuperación/control
  de dispositivos, incluidos trabajos ya admitidos y el salto al worker síncrono.

Estas defensas no sustituyen TCC, la identidad del cliente IPC, el cifrado, Keychain ni la firma.
Las reservas y relojes de autorización son por proceso, no un registro persistente entre reinicios.
El hash detecta PCM idéntico dentro de su ventana, no audio regrabado o modificado ni presencia viva
del propietario. La compuerta de integridad no revierte efectos externos ya iniciados. Esta revisión
no amplía permisos ni certifica seguridad absoluta. Evidencia reproducible en
[Seguridad y autorizaciones](docs/quality/SECURITY_AUTHORIZATION_POLISH.md).

## Respuesta

Una corrección debe añadir una regresión automatizada, pasar las puertas Python/Swift/aceptación y
producir nueva evidencia de release. Ningún secreto se comparte en commits, logs o artefactos.
