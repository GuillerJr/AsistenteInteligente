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

El relay del mismo checkpoint se activó desde la interfaz para compartir sólo
`workspace_write`. Un cliente independiente de biblioteca estándar recibió 401
sin bearer y, autenticado, hizo `server/discover` 2026-07-28, consultó el único
tool exportado y presentó la tarjeta nativa. Tras la aprobación delegada creó
`relay-qualification.txt`: 17 bytes y SHA-256
`5a00bb9d771a098d0bfe72bbf4cce4eb112e9329503fde33514fb543812f13a9`,
coincidentes con el recibo y con una lectura independiente posterior. El primer
probe de ausencia usó por error `root/workspace` en vez de `root/state/workspace`;
no acredita ausencia previa. La suite remota comprueba ese límite y el oráculo
manual conserva expresamente esta limitación. No se repitió la operación.

## Checkpoint 6502786 y repetición con oráculos

La [CI 40755610918](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40755610918)
para `650278610dfa989994852a96be9a0befc98c95a1` aprobó 3112 pruebas Python
macOS, 3066 Ubuntu (46 omisiones) y 469 Swift (una omitida, cero fallos), además
de bundle, paquetes y smoke. Su ZIP tiene SHA-256
`057ab6e40e2c08a69a432af41098bc88619a78b3195a4f1683d2e44da78a9138`.

La nueva importación sí mostró biblioteca y recurso en ambas revisiones. El
primer intento se rechazó porque su manifiesto ya estaba importado por SHA;
se conservó ese registro incompleto y se eligió una fixture nueva. La nueva
biblioteca importada permitió leer los 25 bytes de `RECURSO SKILL JARVIS 1804`
con SHA-256 `393bc6fc6b2aaf9dcc96d6e28e9e813b8ccebea6a9c45bd0a66b5780b8d317aa`.
Preparar argumentos no ejecutó un turno. Editar a v2 conservó el recurso, y
volver a v1 creó revisión nueva deshabilitada.

Codex real recibió dos elementos de texto, 976 bytes: envío original confirmado
y acuse `matched` del app-server, con hashes de identidad, argumentos y contenido
coincidentes. El modelo transcribió el texto remoto y el `operation_id` aleatorio
`97b5e6df81264f1cbe619f99dddcfe5c`, que no estaba en el prompt. El recibo SQLite
independiente coincidió. Una sola llamada produjo ese marcador y no se repitió.
Esto acredita este consumo; la causa exacta del fallo histórico no se puede
reconstruir retroactivamente. Un turno anterior se canceló antes de aprobar al
alcanzar la cuota; el journal confirma que no despachó su marcador.

La medición local superó por unos 3 MiB el presupuesto de 256 MiB durante ese
arranque. Se canceló y cerró la app, se conservaron perfiles y credenciales y se
retiraron únicamente archivos/bundles CI propios reproducibles, con recibos de
hashes. La reanudación permaneció alrededor de 203 MiB. Los metadatos del perfil
muestran recursos descargados por el cliente oficial; no se leyeron sus contenidos,
credenciales ni rollouts. No se atribuye ese incremento a las suites, que corrieron
remotamente.

El nuevo intento público superó las cabeceras repetidas y encontró otro límite:
Microsoft devuelve un `Mcp-Session-Id` válido de 220 bytes. El cliente imponía 128,
cuando el protocolo exige ASCII visible y no fija esa longitud. Se elimina ese
recorte arbitrario manteniendo los límites HTTP por campo y bloque; nuevas
regresiones comprueban reutilización exacta y rechazo de controles/sobretamaño.
Ese último ajuste aún requiere su CI y recorrido público final.

## Interoperabilidad pública y metadatos al inicio

El bundle CI de `ead30c3a87735f2d1aafad1fe386b31577fa2965` conectó Microsoft
Learn con 2025-06-18 explícito y tres herramientas. Una búsqueda pública pasó
por aprobación nativa y terminó con resultado conocido y un recibo exitoso,
sin convertir el contenido remoto en verificado. La respuesta completa tenía
41334 bytes; el app-server acusó los mismos 83959 bytes de los dos elementos de
texto enviados. El modelo devolvió una referencia del resultado, pero dijo no
ver el identificador de operación en la salida larga.

El recibo serializado colocaba `operation_id`, errores y estado después de
`output`. El ajuste siguiente conserva todos los campos/valores y el contenido
completo, y coloca `output` al final. Una regresión de ~41 KiB comprueba los
metadatos en el prefijo, igualdad exacta al reconstruir JSON, contenido íntegro
y una sola ejecución autorizada. No interpreta un acuse como verificación física
ni elimina datos del recibo para forzar el resultado. Falta su CI y repetición.

La skill corregida sobrevivió al reinicio con los mismos 25 bytes/hash. Se
retiró después únicamente ese fixture; la importación histórica incompleta se
conservó. El plugin de prueba terminó también su retirada de versión y eliminación;
sus ZIP originales se conservaron. Se cerraron las conexiones públicas y el
peer propio: salida 0, cero conexiones y cero tareas pendientes. El censo acotado
previo comprobó la salida del servicio, Codex y su auxiliar sin enviarles señales.


## Repetición final del código d935bfc

La [CI 40759557092](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40759557092) del código `d935bfc208e298828ed40445bf46b17073c9c3cc` aprobó Python macOS 3128, Ubuntu 3082 (46 omisiones), Swift 469 casos (una omisión, cero fallos), bundle de desarrollo, wheel/sdist y smoke aislado. Las suites y compilaciones completas corrieron exclusivamente en CI remota.

El bundle exacto repitió una única búsqueda autorizada en Microsoft Learn con
2025-06-18 explícito: un recibo conocido, 41334 bytes de resultado íntegro y dos
elementos de texto con 83959 bytes confirmados por ACK correlacionado. El modelo
transcribió el identificador aleatorio de operación que no aparecía en el prompt,
además de una referencia del resultado. Es una evidencia separada de consumo;
el ACK aislado y el reporte externo siguen sin acreditar verificación física.
Se conserva la omisión de identificador del checkpoint EAD como antecedente, sin
atribuir retroactivamente una causa no observada.

OAuth PKCE/renovación/revocación, SSE, lectura denegada sin despacho, WebKit/puente
y retiro de App, relay por cliente HTTP independiente, versiones y eliminación
de plugins/skills ya tienen sus oráculos por checkpoint. El recurso de la skill
corregida conservó 25 bytes y su hash tras reinicio; su fixture se eliminó y el
registro histórico incompleto se conservó. Los contratos remotos de suscripción
relay comprueban ACK, filtro exacto, revocación, aviso de catálogo y stop obsoleto;
no se afirma una suscripción manual adicional.

La última sesión terminó en 183,041 segundos, dentro del plazo de 300. La app
desconectó la fuente pública y cerró; el socket desapareció. Las tres identidades
censadas del servicio, Codex y host no estaban presentes tras 0,023 segundos, sin
señales del observador. El censo usa PID, inicio con precisión de segundos y
ejecutable, sin handle del kernel; no acredita custodia de árboles arbitrarios.
El peer propio cerró con salida 0, cero conexiones y cero tareas.

La medición final del directorio propio fue de 215744 KiB (aproximadamente
210,7 MiB), bajo 256 MiB; `dasd` marcaba 0 % en esa muestra. El exceso previo de
unos 3 MiB se conserva con su parada y recibos de limpieza de cachés reproducibles.
No se borraron perfiles ni datos del dueño y no se repitieron suites locales.

## Transparencia de privacidad

Al localizar el navegador del acceso OAuth, la selección de Chrome devolvió el
árbol de accesibilidad de una ventana personal. No se abrieron mensajes ni se
usó o exportó su contenido al repositorio o a archivos; el dueño fue informado.
El recorrido continuó mediante el enlace validado, sólo en RAM dentro de Jarvis,
y un IAB aislado cuya pestaña se cerró. Este incidente se registra sin nombres,
remitentes, asuntos ni direcciones personales. Las banderas de exportación del
JSON describen sólo sus metadatos saneados y no niegan esta lectura accidental.

El acceso oficial ChatGPT lo completó el dueño; las credenciales que guarda el
cliente oficial permanecen bajo su gestión y no se inspeccionaron sus archivos.
La afirmación de credenciales sólo RAM corresponde a OAuth MCP, no al almacenamiento
interno de autenticación del proveedor oficial.

## Estado del alcance elegido

El incremento elegido está cualificado (`qualified_selected_increment`): el [registro JSON](PHASE_A_MCP_EXTENSIONS_QUALIFICATION.json) reúne hashes, CI, oráculos y fallos preservados.
Paquetes declarativos propios no equivalen a extensiones ejecutables OpenClaw;
no se acreditan todos sus 177 conectores, todos los servidores, capacidades Apps
no anunciadas, relay OAuth de Internet ni distribución autocontenida/notarizada.
La matriz conserva 51 familias parciales, 29 sin implementar y ninguna completa.
La fase A global sigue abierta y la fase B no ha comenzado.
