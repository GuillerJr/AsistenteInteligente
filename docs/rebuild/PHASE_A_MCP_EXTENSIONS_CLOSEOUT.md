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
- Retirar una conexión también cierra el renderer y la capacidad de su App MCP
  antes del cambio. El recorrido inicial confirmó el DELETE remoto, pero dejó
  visible el iframe antiguo: se conserva ese fallo. Los cierres inciertos
  retienen su identidad y bloquean mutaciones hasta reconciliarse.
- El acceso OAuth pendiente conserva su enlace validado sólo en RAM para
  reabrir el mismo flujo sin buscar pestañas. Finalización, revocación, cambios
  de servidor y cierre retiran el enlace; no contiene tokens de acceso.

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

El primer checkpoint `f185203`, [CI 40749062796](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40749062796),
compiló el bundle y aprobó 456 pruebas Swift (una omitida, cero fallos).
Python macOS aprobó 3 077 y Ubuntu 3 031 con 46 omisiones. Ambos tuvieron
únicamente la misma aserción nueva incorrecta: el constructor stdio rechaza la
versión HTTP con `ValueError`, no `McpError`. Se corrigió la expectativa, sin
cambiar el rechazo productivo. Paquete y smoke de ese checkpoint quedaron sin
ejecutar. No se presenta ese run como CI aprobada; el siguiente incluye también
las nuevas regresiones nativas de cierre y vigencia de Apps MCP.

Quedan por registrar los resultados de esa CI y los recorridos finales nativos:
OAuth PKCE/renovación/revocación, SSE, servidor público compatible, relay con
cliente independiente, paquetes v1/v2/reversión/eliminación y versiones de
skills. La delegación de aprobaciones corresponde sólo a perfiles sintéticos;
no incluye credenciales personales ni permisos TCC.

Fuentes primarias: [transporte MCP 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports),
[ciclo de conexión](https://modelcontextprotocol.io/specification/2025-06-18/basic/lifecycle),
[servidor Microsoft Learn](https://learn.microsoft.com/en-us/training/support/mcp-developer-reference)
y [XNU, señales de procesos](https://raw.githubusercontent.com/apple-oss-distributions/xnu/main/bsd/kern/kern_sig.c).

## Segundo checkpoint remoto y fallos encontrados en la app

La [CI 40751158208](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40751158208)
para `1578f9ec77ed429cf9ab10f7c1f0130253706465` aprobó 3078 casos Python en
macOS y 3032 en Ubuntu (46 omisiones de plataforma). Swift aprobó 465 casos,
con una omisión declarada. También pasaron bundle, wheel/sdist y smoke aislado.
No se ejecutaron esas suites ni compilaciones en el Mac del dueño.

Con ese bundle, en un perfil sintético nuevo y acceso oficial completado por el
dueño, se comprobaron OAuth PKCE, renovación y revocación. El enlace pendiente
se retiró al finalizar; renovar no volvió a conectar automáticamente el MCP.
Una denegación se mostró como conocida sin despachar la lectura, y desconectar
retiró realmente el iframe de la App MCP. El plugin propio se instaló y habilitó,
se actualizó a v2 deshabilitado y volvió a v1 deshabilitado. Los recursos de cada
versión coincidieron con sus hashes y el hook sólo preparó texto en el borrador.
El descriptor MCP del paquete se guardó sin conectar.

El recorrido conserva además tres defectos nuevos, sin convertirlos en éxitos:

- Microsoft Learn usa tres cabeceras `Set-Cookie`. El cliente rechazaba toda
  cabecera repetida. Se prepara descartarlas sin retenerlas ni devolver cookies;
  las cabeceras de encuadre, sesión y autorización siguen sin admitir duplicados.
- Una llamada desde Codex ejecutó exactamente un eco remoto, con recibo y texto
  completos en Jarvis, pero el modelo dijo no tener el texto en su contexto. Se
  añade diagnóstico de envío/acuse por hashes, bytes y códigos saneados. No
  registra texto ni credenciales; un acuse no demuestra consumo por el modelo.
  Un envío incierto tampoco puede generar una segunda respuesta contradictoria.
- «Importar con recursos» conservó sólo el manifiesto de la skill. La revisión
  estaba formada por estados separados y podía perder el modo elegido al abrir
  la hoja. Se prepara una revisión atómica, sin degradar la importación a texto.

Estos arreglos necesitan su propia CI y repetición con el bundle nuevo. La
cualificación del checkpoint anterior no se traslada al árbol modificado.
