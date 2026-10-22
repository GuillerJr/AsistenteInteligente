# Fronteras nativas y remotas — 22 de octubre de 2026

Esta revisión amplía el análisis de Codex `26.1016.51333` y contrasta sus
fronteras con Jarvis. No ejecuta componentes de Codex ni consulta credenciales,
Keychain, permisos del dueño, conversaciones o tráfico de una cuenta real.
La implementación y las pruebas añadidas a Jarvis son propias.

## Evidencia del artefacto

REA inspeccionó por separado tres módulos Mach-O arm64:

| Módulo | Evidencia observada | Alcance de la conclusión |
| --- | --- | --- |
| `browser-use-peer-authorization.node` | 7 dependencias, 90 importaciones y 6 exportaciones retenidas; referencias a audit tokens, `getsockopt` y Security.framework. | Existen enlaces a APIs de identidad de procesos; esto no demuestra que toda ruta de autorización las aplique correctamente. |
| `remote-control-device-key.node` | 6 dependencias, 122 importaciones y 11 exportaciones retenidas; funciones exportadas de crear, consultar, firmar y eliminar una clave; importaciones de Security.framework. | Se identifica la frontera nativa de claves y firmas; no se crean claves, firman desafíos ni comprueban decisiones de un servidor. |
| `input-monitoring-permission.node` | 3 dependencias, 51 importaciones y 4 exportaciones retenidas; referencia a `CGPreflightListenEventAccess`. | Se observa la API para consultar un permiso; no se concede ni modifica ningún permiso TCC. |

Los tres archivos pasan `codesign --verify --strict`. Las importaciones y
exportaciones combinan resultados de `nm` y `dyld_info`: pueden faltar símbolos
eliminados o metadatos que esas herramientas no interpreten. Los conteos
retenidos no son un inventario exhaustivo del comportamiento de las funciones.

La lectura de fuses usa la ruta real del framework renombrado `Codex Framework`,
no el alias ni una supuesta carpeta `Electron Framework`. Confirma integridad
ASAR, carga restringida al ASAR y cifrado de cookies; RunAsNode, NODE_OPTIONS e
Inspector por argumentos están desactivados. No se intentó abrir Inspector ni
alterar esos controles. Estos bits no prueban el comportamiento de otras rutas
de depuración que pudiera implementar la aplicación.

El [manifiesto](CODEX_REA_NATIVE_REMOTE_COVERAGE.json) contiene hashes,
identificadores de evidencia, conteos y limitaciones. Las proyecciones completas
consultadas y la evidencia de la última inspección quedan en `/private/tmp`;
cerrar un target REA no conserva automáticamente los registros de anteriores
targets, por lo que se guardaron aparte sus proyecciones y páginas relevantes.
No se atribuye a estas páginas una decompilación completa.

## Contrato del cliente y contraste con Jarvis

La inspección central de `.vite/build/main-JI9qDiXp.js`, declaración 165,
bytes UTF-8 `[47639, 51205)`, observa solicitudes pendientes ligadas a un ID,
retirada antes de resolver, rechazo y limpieza al cerrar el transporte, y
comprobación del cierre después de esperar un manejador. Es evidencia del
cliente empaquetado. No establece cómo procesa solicitudes el backend remoto.
REA: `ev_cdc544267cb635c647732cfe73cf5426fa9d397d089628291b654c2bf851a24a`.
Los rangos y el resultado completo de esta unidad están referenciados en el
[manifiesto central](CODEX_REA_CORE_CONNECTIONS_COVERAGE.json).

Jarvis ya liga las respuestas IPC a solicitud, nonce, firma y fecha, y su
daemon verifica el audit token del peer para los métodos privilegiados. El
contraste detectó dos problemas distintos en los límites del transporte:

1. **Plazo IPC acumulado.** Los tiempos de socket se reiniciaban en cada
   lectura o escritura. Un peer que enviaba bytes cada 30 ms mantuvo una llamada
   configurada para 150 ms durante más de un segundo. Ahora conectar, enviar y
   recibir comparten un solo plazo monotónico. El socket no bloqueante espera
   disponibilidad con el tiempo restante; los fragmentos y las interrupciones
   no reinician el plazo. `SO_NOSIGPIPE` convierte el cierre del peer durante
   una escritura en un error controlado, sin terminar el proceso.
2. **Consistencia de respuestas NVIDIA en flujo.** Se concatenaban eventos
   aunque cambiara el identificador de respuesta, el modelo declarado o el
   índice de la elección. Los campos opcionales `id` y `model`, una vez
   presentes, deben permanecer estables; una elección explícita debe ser la
   primera. También se validan los eventos que solo contienen uso. El fragmento
   ajeno se rechaza antes de publicarlo y no provoca un reintento tras texto
   visible. Se conserva compatibilidad con servidores que omiten esos campos.

La consistencia de metadatos del stream no equivale a autenticar un servidor ni
a validar la veracidad de la respuesta. NVIDIA sigue siendo el único proveedor.

## Pruebas

Las pruebas usan sockets Unix temporales y un transporte HTTP simulado. No
abren el socket real de Jarvis ni envían contenido a NVIDIA.

- Respuesta IPC fragmentada, tanto JSON legado como marcos autenticados,
  incluyendo un mensaje que requiere varios marcos.
- Un peer que gotea bytes no prolonga el plazo; un peer que deja de leer tampoco
  bloquea la escritura. El cierre anticipado termina con un error controlado.
- Identidades NVIDIA cambiantes o malformadas, índice de elección inválido,
  metadatos de uso de otra respuesta, campos omitidos y metadatos que llegan
  después del primer fragmento.
- Las siete regresiones negativas de identidad fallaron antes de la corrección;
  las pruebas de goteo reprodujeron el exceso del plazo anterior.

Pasaron 96 pruebas Python de NVIDIA e identidad del peer, 38 pruebas IPC Swift,
`ruff` y la comprobación de espacios del diff. Las pruebas con sockets se
ejecutaron fuera del sandbox; dentro, `bind` está bloqueado y no representa un
fallo del producto. La calificación completa del lote se registra por separado.
No se presentan los fixtures de Jarvis como pruebas dinámicas de Codex.

## Lo que sigue abierto

No se ha decompilado toda la implementación de estos módulos, resuelto sus
llamadas indirectas ni analizado todos los ejecutables nativos incluidos en
Codex. Tampoco se tiene el código o una prueba del backend privado. La revisión
puede continuar por nuevas fronteras concretas; los símbolos, las firmas y los
contratos del cliente no permiten declarar exhaustividad ni equivalencia.
