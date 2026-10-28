# Cierre de la cualificación MCP, plugins y skills

Continuación del 28-10-2026. Este registro separa los cambios preparados, la CI
remota y los recorridos manuales de la app con datos sintéticos. No cierra la
fase A global ni acredita todos los servidores o paquetes de terceros.

## Correcciones preparadas

- Cierre de Codex: ante `EPERM`, esperar con plazo al hijo propio y exigir una
  comprobación fresca `ESRCH` del grupo exacto antes de liberar su custodia. Un
  grupo presente o inaccesible conserva el bloqueo. El diagnóstico mantiene
  fase y errno numérico sin rutas, argumentos ni mensajes del sistema.
- Selección explícita de Streamable HTTP 2025-06-18, además de los transportes
  ya implementados. El primer intento nativo a Microsoft Learn con 2025-11-25
  fue rechazado: una consulta de diagnóstico pública confirmó que el servidor
  respondía 2025-06-18. Se conserva el rechazo y no se añade downgrade automático.
- Una denegación conocida de un plan del host, antes de producir recibos o
  despachar su acción, conserva `approval_denied/known`. Cancelación, desconexión
  y efectos previos no adquieren esa clasificación.
- Eliminar un agente con plugins instalados queda bloqueado hasta retirarlos
  explícitamente, evitando registros huérfanos.
- Las lecturas tardías de plugins y skills revalidan agente, revisión, recurso,
  generación y runtime antes de publicar contenido o incorporarlo al borrador.

El errno del fallo histórico de CI no se conservó. La carrera de salida es
consistente con el comportamiento de `killpg` en XNU, pero no demuestra
retrospectivamente cuál fue aquel errno. Las regresiones comprueban tanto la
carrera de salida como denegaciones persistentes y grupos todavía presentes.

## Recorrido manual inicial

Se descargó exclusivamente el bundle de desarrollo de la CI `40746598454`,
commit `4f0e847e6490d4ec5a637466f302c809531127e2`. Archivo comprimido y binario se
verificaron contra su procedencia. Se creó una identidad ad hoc de prueba con
un lanzador de argumentos fijos, perfil y proveedor nuevos, sin reutilizar
credenciales ni datos personales. La nueva firma no es Developer ID.

En el checkpoint inicial se observaron desde la interfaz:

- HTTP 2025-11-25 conectado al peer propio; dos recursos, un prompt y una
  plantilla publicados. Guardar la configuración no conectó automáticamente.
- Rechazo de lectura con cero `resources/read` en el journal independiente.
  Se conservó el fallo de presentación que lo mostraba como incierto.
- Lectura autorizada de `RECURSO JARVIS 7319`, reportada como contenido externo
  sin comprobación física independiente.
- Prompt con argumento `PROMPT JARVIS 4826`, añadido únicamente al borrador con
  etiqueta de datos externos. No se envió un turno del modelo.
- Plantilla `TOPACIO 4826` expandida a `TOPACIO%204826`; su lectura requirió
  otra autorización exacta.
- App MCP realmente renderizada en WebKit: `APP JARVIS 8624`, puente preparado,
  solicitud `APP ECO 6421` con aprobación nativa separada y resultado
  `ECO EXTERNO APP ECO 6421` en el iframe. El journal registró una sola llamada.
  Los marcadores HTML y `_meta` privado no aparecieron en la SQLite del perfil.

Estas observaciones pertenecen al checkpoint anterior a las correcciones. Los
recorridos finales con el bundle nuevo y las restantes variantes se registrarán
después de ejecutarlos; no se anticipa su aprobación.

## Validación y pendientes de este cierre

Se añadieron regresiones de cierre Codex, integración HTTP/OAuth/Apps/relay,
plugins y skills, borrado de agentes y respuestas nativas tardías. Las suites
Python, Swift, paquete y bundle se ejecutarán exclusivamente en CI remota para
el commit exacto. Los hooks locales sólo comprueban fuentes e identidad.

Quedan por registrar los resultados de esa CI y los recorridos finales nativos:
OAuth PKCE/renovación/revocación, SSE, servidor público compatible, relay con
cliente independiente, paquetes v1/v2/reversión/eliminación y versiones de
skills. La delegación de aprobaciones corresponde sólo a perfiles sintéticos;
no incluye credenciales personales ni permisos TCC.

Fuentes primarias: [transporte MCP 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports),
[ciclo de conexión](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle),
[servidor Microsoft Learn](https://learn.microsoft.com/en-us/training/support/mcp-developer-reference)
y [XNU, señales de procesos](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/kern/kern_sig.c).
