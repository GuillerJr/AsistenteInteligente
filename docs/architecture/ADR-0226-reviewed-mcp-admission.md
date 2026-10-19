# ADR-0226 — Admisión explícita de MCP externo

Estado: implementado en código; integración real pendiente de elección y revisión.
Fecha: 2026-09-14.

## Contexto

El punto 10 comienza por MCP externo de solo lectura, elegido por el propietario. No implica
activar dispositivos, MLX distribuido, protocolos nuevos ni realizar pruebas con su voz.
El host stdio existente importaba todas las herramientas descubiertas, clasificaba su visibilidad
por palabras en sus nombres y un servidor defectuoso podía abortar el arranque del daemon.

## Decisión

- `enabled` es falso por defecto; `read_only` es verdadero y rechaza capacidades de escritura.
- La configuración local privada declara nombres exactos en `approved_tools` y el SHA-256 de
  cada `inputSchema` normalizado con las restricciones locales existentes. No hay comodines.
- `application_bundle_ids` selecciona la visibilidad por aplicación explícita. Se elimina la
  clasificación léxica; el catálogo general no se abre a aplicaciones desconocidas.
- Una lista vacía de herramientas aprobadas o aplicaciones impide iniciar el proceso.
- Herramientas adicionales no se importan. Una herramienta aprobada ausente o con esquema
  distinto pone ese servidor en cuarentena. Los nombres duplicados y cursores repetidos fallan.
- `readOnlyHint=false` contradice la revisión de solo lectura; `true` o su ausencia no otorgan
  autoridad. La revisión de implementación y los permisos del servicio siguen siendo necesarios.
- Toda llamada MCP stdio requiere confirmación de un solo uso, aunque su riesgo sea medio:
  una consulta puede transmitir información al proveedor. No se admite la vía rápida de intención local.
- Fallos de configuración invalidan MCP, no el resto de Jarvis. Fallos de un peer invalidan
  solo ese peer. Los errores de integridad de auditoría siguen propagándose y bloqueando el daemon.
- Un cambio de catálogo notificado invalida inmediatamente la disponibilidad, incluidos handlers
  ya registrados. No hay reconexión, reintento, nueva aprobación ni instalación automática.
- Cancelar arranque o llamada cierra el cliente y termina su proceso directo; los protocolos
  ambiguos y flags de error no booleanos se rechazan.

## Diagnóstico y revisión

`mcp-check` revisa la configuración stdio sin procesos, red, Keychain ni capturas. Un estado
`awaiting_connection` significa solamente que las condiciones estáticas están presentes.
`mcp-schema ARCHIVO` calcula una huella de un inputSchema copiado y revisado manualmente; no
consulta servicios ni aprueba nada. `mcp-status` lee `mcp.status` por IPC autenticado, sin crear
claves ni arrancar el daemon. Los informes no incluyen argumentos, entorno, rutas ni resultados.

## Límites y promoción

Esta fase endurece una integración existente; no cambia `JarvisV1Scope.json`, no descongela la
release y no declara capacidades estables nuevas. Los Capability Packs HTTPS usan otro runtime:
no quedan certificados por estos informes. Tampoco se implementa un transporte HTTP nuevo aquí.

El SHA-256 fija el contrato de entrada, **no la implementación ni la identidad del servidor**.
Un proceso stdio corre con permisos de la cuenta: la separación de proceso no es App Sandbox ni
una restricción de red/archivos. No se deben arrancar servidores desconocidos, lanzadores que
descarguen `latest`, ni pasar secretos en argumentos. La revisión de artefacto/versionado y las
credenciales con permisos de lectura son requisitos de una conexión real. Un servidor malicioso
puede mentir sobre sus efectos o cambiar comportamiento sin cambiar el esquema.

La selección por aplicación reduce la superficie del planner; la autoridad sigue en el broker,
el ejecutor y las confirmaciones. No se incorpora automáticamente el catálogo a otros flujos,
como el terminal de ingeniería, que tienen su propio conjunto de herramientas.

La promoción real de un servidor requiere: elección del propietario, revisión de origen/versión,
transporte compatible probado, alcance de datos, herramientas y esquemas exactos, permisos de
lectura del proveedor, prueba positiva y negativa, amenaza residual aceptada y aprobación expresa.
Las pruebas sintéticas no sustituyen esos pasos. No se ha conectado Context7 ni GitHub.

## Fuentes

- [MCP tools 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/server/tools):
  nombres, paginación, cambios del catálogo y anotaciones no confiables.
- [MCP lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle):
  negociación y cierre.
- [Context7 oficial](https://github.com/upstash/context7): candidato para consultas de documentación.
- [GitHub MCP oficial](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md):
  segunda opción con modo de solo lectura y selección de herramientas.
