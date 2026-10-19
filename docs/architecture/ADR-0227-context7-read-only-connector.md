# ADR-0227 — Context7 como primer MCP externo de documentación

Fecha: 2026-09-14. Estado: implementado, conectado y probado por CLI; pack instalado y cargado
por la app tras una actualización local autorizada. Evidencia y copia recuperable en
[CONTEXT7_INTEGRATION](../quality/CONTEXT7_INTEGRATION.md).

## Decisión

El propietario eligió Context7 tras priorizar MCP de solo lectura. Se reutiliza el runtime de
Capability Packs HTTPS, no se inicia un servidor stdio ni se descarga Node/npm/código externo.
El único destino es `https://mcp.context7.com/mcp`, con verificación TLS por hostname y conexión
fijada a una dirección DNS validada como pública. No hay redirects ni credenciales en URLs.

El pack `context7-docs`, versión 1.0.0, declara únicamente `web_read` y estas dos herramientas:

- `resolve-library-id`, parámetros `query` y `libraryName`.
- `query-docs`, parámetros `query` y `libraryId`.

La exposición local normaliza guiones a underscores y rechaza colisiones. Los parámetros camelCase
se conservan porque forman parte del contrato oficial. Los esquemas locales son cerrados y limitan
consultas a 512 caracteres e identificadores a 160. Ambas herramientas son riesgo alto para exigir
confirmación de un solo uso antes de transmitir datos, aunque no escriban en el servicio.

La Skill local se selecciona al pedir Context7 explícitamente; no se activa por cualquier pregunta
de programación. No concede permisos ni se envía como instrucciones a NVIDIA. En el CLI de ingeniería
la política `offline` sigue bloqueando plugins/MCP, también después de incorporar starter tools.
Se necesita `--research-policy public_web` además de pedir Context7 y aprobar la consulta.

## Contrato remoto

Las consultas reales comprobaron el MCP stateless `2026-07-28`. Cada petición lleva dentro de
`params._meta` los campos obligatorios `io.modelcontextprotocol/protocolVersion` y
`io.modelcontextprotocol/clientCapabilities`, además de clientInfo. Se conservan headers de método
y nombre. El servidor reportó versión 4.1.1; esa identidad es informativa, no una prueba criptográfica.

Se fija el SHA-256 del inputSchema normalizado con los límites de admisión locales existentes:

| Herramienta | SHA-256 revisado |
|---|---|
| resolve-library-id | ce5be95bc6b75bdc271aa6d2f962623e6e38f111fddf687a56a8fa99ae523a0c |
| query-docs | 70cb9330ac9b282a278068ddf44df672a4aef20214be93b5ccbf58a78a6fae70 |

Antes de cada llamada se vuelve a consultar un catálogo acotado y se comparan las dos huellas.
Una discrepancia, ausencia, duplicado, paginación o anotación que contradiga solo lectura bloquea
la llamada. No se reescriben huellas, amplían tools ni instalan versiones automáticamente.

El campo opcional `remote_input_schema_sha256` se omite al serializar si no tiene valor; así los
packs anteriores conservan su representación, checksum y sello. El pack nuevo exige un runtime
que conozca el campo. No debe afirmarse que una app empaquetada anterior lo ha cargado.

## Fronteras y errores

Se rechazan consultas con credenciales reconocibles, emails, URLs, rutas personales, controles,
bloques de código o tamaño excesivo antes de solicitar aprobación y nuevamente antes de transmitir.
El filtro es preventivo, no una garantía de detección de información privada: texto empresarial
confidencial puede parecer una pregunta común. Sigue siendo necesaria la revisión humana.

La salida se valida y acota como datos no confiables. `isError=true` nunca se convierte en éxito;
MRTR/`input_required`, respuestas ambiguas y streams con interacciones adicionales se rechazan.
No hay ejecución de instrucciones, descarga de imágenes ni apertura automática de enlaces del resultado.
Las huellas fijan contratos, no implementación: el comportamiento del servicio remoto sigue siendo
una confianza explícita en el proveedor. Context7 puede registrar la consulta y la IP del cliente.

Las pruebas reales fueron anónimas; no fue necesaria una clave de Context7 y no se creó una cuenta.
No se promete disponibilidad ni cuota futura. Si se necesita autenticación, se debe autorizar
aparte y guardar el Bearer mediante el mecanismo Keychain existente, nunca en chat ni en el pack.

## Diagnósticos

- `context7-catalogue`: una consulta de metadatos; imprime nombres/huellas, no aprueba ni instala.
- `probe-context7`: dos consultas fijas sobre Python/asyncio; comprueba catálogo antes de cada una
  y reporta solo tamaños y resultados, sin contenido privado ni parámetros de usuario.
- `plugins-verify`: verifica sellos locales; no prueba red ni que la app haya cargado el pack.
- `plugins-simulate context7-docs --request 'consulta context7 sobre Python'`: selección sin ejecución.

El consentimiento para estos diagnósticos no equivale a aprobación de consultas futuras propuestas
por un modelo. Se conservan el broker y las aprobaciones por llamada.

## Fuentes primarias

- [Context7 oficial](https://github.com/upstash/context7).
- [Herramientas y contratos de Context7](https://github.com/upstash/context7/blob/master/packages/mcp/src/index.ts).
- [Campos por petición de MCP 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/basic).
