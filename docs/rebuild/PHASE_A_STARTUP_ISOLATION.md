# Arranque aislado del servicio — 26-10-2026

La mitigación del arranque queda **implementada y verificada** para las rutas
reproducidas. Este incremento cierra el trabajo operativo de aislamiento: la app
usa el intérprete elegido de forma canónica, evita la consulta junto al enlace de
venv reproducido y desactiva los hooks de `site`. Conserva el arranque y cierre
del mismo servicio. No declara completa
la fase A ni inicia la fase B.

El [registro verificable](PHASE_A_STARTUP_ISOLATION.json) conserva identidades,
pruebas, tiempos y límites. Todos los procesos, perfiles y entradas utilizados
para esta investigación fueron sintéticos y propios.

## Qué se sabe del incidente histórico

El [registro anterior](PHASE_A_MEMORY_COPY_LIVE.json), en `runtime_isolation`,
sitúa un intento bloqueado dentro de la inicialización de rutas de CPython,
antes de un frame de Jarvis o de su socket. Conserva el SHA-256 de aquella
muestra: `c5939b256e38c0bc2e0ed184305d1a6b3e9c33e19606e5991773cab8c233a62d`.
Los mismos 38 módulos arrancaron con Python canónico y una copia privada de
fuentes idénticas por bytes.

Ese registro no acredita el archivo concreto ni la causa del sistema operativo.
La investigación nueva reproduce y mitiga mecanismos de esa frontera, pero no
afirma que el incidente original fuera un FIFO, una descarga de iCloud, SQLite o
la memoria de Jarvis. Esta limitación de atribución histórica no deja otra
implementación pendiente en este incremento.

## Reproducción causal

| Entrada propia controlada | Antes e intervención | Nuevo arranque aislado |
| --- | --- | --- |
| Intérprete enlazado dentro de un venv con `pyvenv.cfg` FIFO | No aparece el primer frame propio. Liberar únicamente ese FIFO hace avanzar al mismo PID. | El mismo intérprete seleccionado, resuelto a su ruta canónica, evita ese descubrimiento. El proceso nativo real llega a `health` conservando el FIFO sin consultar. |
| `PYTHONUSERBASE` con un `usercustomize` propio bloqueado en otro FIFO | El hook se ejecuta y todavía no existe el socket de salud. Liberar el FIFO permite que ese mismo PID llegue a `health`. | El arranque aislado llega a salud sin entrar en el hook. |
| `PYTHONHOME` inválido y propio | El proceso termina antes del primer frame propio. | El mismo binario elegido produce el frame al ignorar esa configuración; no se busca otro intérprete. |

Una muestra nativa del primer caso contiene la cadena
`pymain_init → Py_InitializeFromConfig → config_init_import → getpath_readlines
→ fread → __read_nocancel`. Su SHA-256 es
`0185be514ee658b90562f90915f13aa62c510c58a0504356d088b281f6fa4b9a`.
Es una prueba del bloqueo sintético controlado, no una identificación del archivo
histórico. Las pruebas distinguen la liberación del PID original de la comparación
con un proceso nuevo que usa el plan corregido.

## Asentamiento del fixture

Una ejecución completa posterior registró un `TimeoutExpired` del caso FIFO
después de publicar su contenido: 2319 pruebas pasaron y una falló. Cien
réplicas nominales aisladas no reprodujeron aquel fallo. Hacer persistente la
configuración tampoco bastó: otra repetición bajo carga volvió a agotar el plazo.
No se observó una segunda lectura real de CPython ni se atribuye el fallo a ella.

El ensayo inicial confundía emparejar los extremos del FIFO con acreditar que el
lector había consumido su contenido. Ahora el productor conserva abierto su
descriptor hasta que `FIONREAD` confirma que se consumieron los bytes escritos;
entonces envía EOF. Antes publica los mismos bytes como configuración regular
para permitir consultas posteriores. Se comprueba que ese renombre, por sí solo,
no hace aparecer el marcador del lector ya bloqueado. Otro caso determinista
comprueba dos lecturas de la misma configuración.

Se conserva un único plazo total de **3 s desde escribir hasta recibir el primer
frame**; la espera del ACK y la salida comparten ese plazo. No se amplió el TTL
ni se cambió código productivo para corregir este fixture. Los temporales usan
`/private/tmp` en Darwin y `/tmp` en Linux.

Con el ACK, veinte ejecuciones completas de los cuatro casos pasaron **80/80**
bajo dos trabajadores de CPU limitados a 40 s, vivos al comenzar cada ejecución.
El tiempo acumulado de esas ejecuciones fue 19,850485 s. Una ejecución final sin
carga pasó los cuatro casos en 0,71 s. Los registros y hashes se conservan en el
JSON; la aprobación global de publicación se registra por separado.

## Contrato implementado

`ServiceLaunchPlan` resuelve los enlaces del ejecutable que ya eligió el dueño y
exige que siga siendo ejecutable. No descubre ni sustituye otro Python. El núcleo
actual no tiene dependencias Python externas.

La llamada usa `-I -S -B -u -c` y un bootstrap fijo de la biblioteca estándar.
Importa primero `runpy` y `sys`, recibe la ruta de fuente como un argumento
separado, la inserta explícitamente y ejecuta `jarvis_core.service`. No interpola
la ruta como código ni usa shell, `eval` o hooks del artefacto. `-I` aísla rutas y
variables Python; `-S` también desactiva los hooks globales de `site`; `-B` evita
escrituras de bytecode en la fuente y `-u` mantiene el modo sin búfer.

El directorio de trabajo es `/`. Sólo se transmiten, si existen, `PATH`, `HOME`,
`TMPDIR`, `LANG`, `LC_ALL`, `LC_CTYPE`, `TZ` y `__CF_USER_TEXT_ENCODING`.
`PYTHONHOME`, `PYTHONPATH`, `PYTHONUSERBASE`, `PYTHONSTARTUP` y ajustes del cargador
no se heredan. La fuente elegida se transmite por argv. La política privada del
cliente oficial Codex permanece independiente; no se importan credenciales.

`FoundationServiceChild` aplica ese plan. Se conservan los argumentos de estado,
Codex, proveedor explícito y admisión restaurada. La opción nativa
`--recovery-only` no se envía al servicio. stdout y stderr del producto siguen
dirigidos al dispositivo nulo; no se exponen trazas ni contenido del entorno.

## Verificación y tiempos

- Cuatro pruebas causales Python: **4 aprobadas en 0,71 s**. Comprueban bloqueo,
  intervención y avance del mismo PID, ACK de consumo y dos lecturas de la
  configuración, aislamiento y ausencia de relanzamiento. El registro conserva
  también la cualificación inicial de tres casos y el seguimiento del fixture.
- Bootstrap nativo: **8 aprobadas en 8,319 s**, con tres casos nuevos y cinco
  regresiones existentes. Incluyen salud del servicio real ante el FIFO, fuente
  transmitida como argumento, entorno filtrado, ausencia de fallback, proveedor
  explícito y recuperación lectora de un hijo deliberadamente lento.
- Contratos de servicio, login y proveedor: **127 aprobadas en 33,97 s** con peers
  propios que simulan el protocolo oficial. No acreditan una cuenta, entitlement
  o inferencia reales.
- Ruff y comprobación de espacios del diff: aprobados para los archivos del
  incremento. Estos resultados focalizados no sustituyen los controles globales
  de publicación.

En la medición inicial, el primer frame canónico tardó **0,037186 s**; la salud
con el hook de usuario propio presente, **0,316270 s**; el servicio lanzado por el
proceso nativo con el FIFO de configuración presente, **0,314011 s**. Son mediciones de esos fixtures,
no un límite universal de latencia. Las demoras de lectura del intérprete o de la
fuente seleccionada siguen dependiendo del sistema de archivos.
En el focal final del fixture corregido se midieron 0,023848 s para el primer
frame y 0,227067 s para la salud con el hook presente.

La evidencia y los hashes de los cinco archivos están en el JSON. El informe
original del incremento es
`/private/tmp/jarvis-startup-investigation-20261026.json`, SHA-256
`7e0370efff027cea2d9cb04bf1e845f7892359113274cb55ae3d0be29454fde0`.
Un primer intento con restricciones del ejecutor no pudo crear sus propios
sockets Unix; los recorridos cualificados se repitieron con permiso limitado al
IPC de estos fixtures, sin modificar el producto para eludir el fallo.

No se leyó `auth.json`, historial personal ni perfiles privados del dueño. No se
usaron cuentas, inferencia, aprobaciones reales ni la app instalada. El cambio no
convierte el bundle de desarrollo en una distribución autocontenida.
