# OC24 — MCP, extensiones y skills

Referencia de lectura: OpenClaw `1e3ff306abaff29f03e7668d53b01c07f10294ac`.
Se adquirieron siete particiones inertes: HTTP, OAuth, Apps, relay, instalación,
lifecycle de plugins y versiones de skills. REA analizó 26 fuentes TypeScript y
sus importaciones locales seleccionadas; no ejecutó OpenClaw ni sus paquetes.
La implementación Jarvis se escribió en este repositorio y no importa ese árbol.

[Identidades, hashes, fuentes, Evidence IDs y trazas](REA_OC24_SUMMARY.json)
conservan la adquisición y la exportación local de 38 792 259 bytes. Las fuentes
adquiridas se volvieron a verificar por SHA-256. La exportación completa sigue
fuera del producto; el resumen y sus hashes permiten identificarla.

## Contratos que se contrastaron

- HTTP necesita una identidad de servidor y transporte explícitos, límites y
  custodia hasta cerrar cada conexión. Reconectar o repetir una operación después
  de perder su respuesta puede duplicar un efecto.
- OAuth separa recurso, emisor, client ID, scopes y credencial. Se conservan el
  origen revisado y la frontera de renovación; un texto del servidor no permite
  enviar tokens a un destino nuevo.
- Las vistas MCP tienen una concesión limitada por servidor, sesión, agente,
  recurso y tiempo. El URI identifica un recurso del peer: no autoriza leer una
  ruta local ni abrir su URL directamente. Su HTML y metadatos privados requieren
  un canal distinto del contexto del modelo.
- Instalar, publicar y actualizar un paquete son operaciones distintas de cargar
  contexto y de conectar programas MCP. Las transacciones y sus snapshots deben
  comprobar la custodia inmediatamente antes de publicar o revertir.
- La selección de una skill debe conservar versión, contenido, recursos y
  procedencia. El cambio o la reversión no concede permisos de herramientas.

Son decisiones propias contrastadas con las partes seleccionadas; no son una
transcripción de la arquitectura ni una declaración de equivalencia de código.

## Protocolos y diseño propios

Jarvis combina stdio, Streamable HTTP 2025-11-25/2026-07-28 y SSE legado explícito
2024-11-05. No prueba versiones por fallback. Sus recursos y prompts pasan por
el Runtime existente; las Apps nativas tienen un iframe de origen opaco y un
puente de capacidades limitado. El relay local publica sólo herramientas elegidas
con credencial en RAM; no se presenta como un proveedor OAuth remoto.

Las extensiones propias usan `jarvis-extension/1`: datos, recursos, prompts,
skills y descriptores MCP declarados. No cargan JavaScript/Python arbitrario del
paquete ni son paquetes OpenClaw compatibles. Los hooks MCP son propuestas
exactas sometidas a la autoridad común. Las skills conservan snapshots y recursos,
y su invocación produce un borrador, sin ejecutarlo automáticamente.

## Límites de esta investigación

Se conservaron **6446 incógnitas semánticas**. Las trazas de relay y skills son
parciales; los siete grafos mantienen cobertura semántica desconocida. Las
relaciones hacia fuentes no seleccionadas no se reconstruyeron por inferencia.
No se revisaron todas las dependencias, módulos, plataformas ni comportamientos
dinámicos de OpenClaw. Las cifras de [cobertura](REA_COVERAGE.json) contabilizan
rutas seleccionadas, no una auditoría exhaustiva ni la fase A completa.

La validación del producto se documenta por separado: compilar o pasar pruebas
sintéticas no demuestra acceso a una cuenta externa ni paridad de toda la base.

## Fuentes primarias

- [Transporte MCP 2025](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
- [Transporte MCP 2026](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http).
- [Autorización MCP](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization).
- [Apps MCP 2026-01-26](https://github.com/modelcontextprotocol/ext-apps/blob/main/specification/2026-01-26/apps.mdx).
- [Plantillas URI, RFC 6570](https://www.rfc-editor.org/rfc/rfc6570.html).
- [Referencias JSON Schema sin recuperación implícita](https://python-jsonschema.readthedocs.io/en/stable/referencing/).
