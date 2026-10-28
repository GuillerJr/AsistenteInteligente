# MCP, plugins y skills — fase A

Incremento del 28-10-2026. Investigación por siete partes con REA y código propio:
[fuentes y límites](REA_OC24_MCP_EXTENSIONS.md). No se incorpora OpenClaw como
implementación ni como dependencia del producto.

## Funcionalidad conectada

- MCP stdio, Streamable HTTP 2025-11-25/2026-07-28 y SSE legado 2024-11-05
  seleccionados explícitamente. Sesiones, notificaciones, paginación, cierre,
  límites y comprobación de autorización inmediatamente antes del primer byte.
- OAuth con descubrimiento del recurso/emisor, elección explícita entre varios
  emisores, PKCE S256, state, callback local, audiencia, scopes y renovación o
  revocación explícitas. Tokens, códigos y concesiones permanecen en RAM. Cambiar
  la autorización retira las vistas y conexiones anteriores.
- Catálogo de herramientas, recursos, prompts y plantillas RFC 6570. La expansión
  sólo construye una etiqueta URI: su lectura se hace a través del peer MCP con
  aprobación exacta; no abre archivos ni conexiones derivadas del URI.
- Esquemas JSON Schema draft 7/2020-12 con referencias locales y validación
  compleja en un worker propio con plazo y CPU limitados. Las referencias externas
  nunca se recuperan. El worker recibe únicamente dependencias revisadas; conserva
  aislamiento Python y no ejecuta código de una extensión.
- Apps MCP en WebKit con iframe de origen opaco, políticas de red revisadas,
  almacenamiento no persistente y puente limitado a herramientas/recursos del
  servidor elegido. El HTML y `_meta` privados no llegan al modelo ni a recibos
  durables. El cierre, cambio de contexto, catálogo o autorización retira la vista.
- Servidor/relay local autenticado: exportaciones elegidas, credencial en RAM,
  solicitudes correlacionadas y suscripciones. Cada operación entra en el mismo
  Runtime y autoridad, incluso las herramientas normalmente de sólo lectura.
  Stop/revocación cancelan únicamente sus ejecuciones y esperan su limpieza.
- Paquetes propios `jarvis-extension/1`: inspección de ZIP/TAR sin comprimir,
  hashes, procedencia declarada no verificada, revisión y publicación con CAS.
  Instalación/actualización desactivadas inicialmente, activación, desactivación,
  eliminación, historial, reversión y retirada explícita de versiones antiguas.
- Contribuciones de recursos, prompts, skills y hooks. Los hooks MCP antes/después
  del turno y los elegidos desde la interfaz usan la autoridad común. Configurar
  un descriptor MCP requiere otra revisión y no conecta su programa.
- Skills con metadatos, argumentos, recursos por hash, biblioteca importable,
  snapshots, reversión y retirada de versiones. Invocar una skill prepara contexto
  revisable; no envía un turno ni ejecuta archivos automáticamente.
- Respaldo/doctor/restauración incluyen el registro de plugins y los recursos e
  historiales de skills. La restauración mantiene plugins y skills desactivados;
  conserva compatibilidad con inventarios históricos de siete roles.

## Límites explícitos

Son extensiones declarativas propias; no son paquetes JavaScript/Python de
OpenClaw compatibles ni se ejecuta código arbitrario del archivo instalado.
Las capacidades opcionales no anunciadas de Apps —sampling, cámara, micrófono,
ventanas emergentes, frames anidados y navegación externa— se rechazan. El relay
se limita a loopback y no se presenta como un servidor OAuth de Internet.

Una confirmación de transporte o un reporte MCP no acreditan el efecto físico de
un servidor. Los recibos conservan `verified: false` y la incertidumbre. Cancelar,
perder una respuesta o recibir una petición duplicada nunca autoriza replay.

No se conectaron cuentas comerciales MCP implícitamente. Codex con ChatGPT sigue
siendo el proveedor de conversación elegido. No hay inferencia, memoria ni audio
a NVIDIA ni cambio oculto de proveedor.

## Cualificación

La implementación está en comprobación nativa y CI. Pasaron inicialmente 78
pruebas focales MCP y 79 de extensiones/skills, además de los contratos previos de
chat y MCP. Los recibos identifican cada árbol comprobado; no sustituyen la suite
remota del commit final ni un recorrido real de la app.

La [CI del código `c74f924`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40729616173)
terminó aprobada en ambos sistemas: Python Ubuntu 3 009 aprobadas/46 omitidas,
Python macOS 3 055 aprobadas, Swift 452 pruebas/1 omitida/0 fallos. También
pasaron el bundle nativo de desarrollo, wheel, sdist y smoke aislado. Los guards
confirmaron limpieza y fuentes sin cambios. La verificación nativa del respaldo
acepta ahora los ocho roles actuales, manteniendo compatibilidad histórica y
rechazo de entradas inválidas. Esta CI no sustituye los recorridos de la app
frente a servicios externos ni cierra la fase A global.

La compilación local se detuvo por `global_storage_guard` tras 13,667 s y un pico
de 103 165 952 bytes dentro de su volumen. Se conservaron el resultado fallido y
una reconciliación separada del desmontaje confirmado. Se retiró únicamente su
imagen propia de 383 782 912 bytes. No se atribuye a ella la caída global de espacio
libre ni se desactiva el guard. La compilación y XCTest completos se trasladan a
CI; ésta conserva un bundle de desarrollo trazable para la comprobación local.

El diagnóstico posterior identificó un temporal SQLite de `dasd` creciendo a
125,58 MB/s; tras el cierre normal autorizado del proceso cesó el llenado
acelerado durante el intervalo medido. La causa que disparó esa actividad aún
no se conoce. Se conservan medidas, límites y la distinción frente al consumo
propio de la compilación en el [informe de almacenamiento](INCIDENTE_ALMACENAMIENTO_20261028.md).

El primer arranque del bundle de CI detectó además una incompatibilidad real:
el servicio anuncia la versión SSE 2024-11-05 y el cliente sólo reconocía las
dos posteriores. Se corrigió el catálogo nativo, se restringió esa versión al
transporte SSE y se añadieron regresiones de arranque y compatibilidad. La
ejecución de esas regresiones pasó en la CI citada. El nuevo recorrido nativo
sigue pendiente; la comprobación sintáctica inicial no se presenta como
compilación ni prueba funcional.

La fase A global continúa abierta: distribución autocontenida y otras familias
se cualifican por separado. Este documento no declara revisión exhaustiva de
OpenClaw, equivalencia de todos sus plugins ni una app de iPhone.
