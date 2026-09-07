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

## Respuesta

Una corrección debe añadir una regresión automatizada, pasar las puertas Python/Swift/aceptación y
producir nueva evidencia de release. Ningún secreto se comparte en commits, logs o artefactos.
