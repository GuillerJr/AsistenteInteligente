# Fase A: copia explícita de memoria conservada

El núcleo, la compatibilidad SQLite, el coordinador del host y la interfaz nativa
están integrados. Las decisiones de las pruebas automáticas son sintéticas. Una
copia autorizada desde la app de dos notas de prueba quedó verificada por separado;
su alcance se registra abajo. No cierra A-71 ni la fase A. La fase B no ha comenzado.

## Ampliación a memoria fría WAL y journal

El inventario y la revisión leen el estado confirmado de familias compatibles
DELETE, WAL y journals íntegros mediante imágenes privadas. Las fuentes hermanas
participan en el presupuesto agregado y el mismo corte del host. El DTO presenta
el tipo de imagen y si la copia exigirá recuperación SQLite; las familias ambiguas
o dañadas requieren el recorrido de reparación separado.

La copia autorizada delega únicamente esa familia congelada al store de memoria.
Revalida identidad, autoridad, plazo y cancelación antes de abrir y antes de COMMIT.
Inventario y revisión conservan los archivos; tras aprobar, abrir SQLite puede
recuperar y consumir sus sidecars. La garantía posterior es conservar las notas
originales y verificar la copia/procedencia, no mantener bytes y metadatos físicos
de una base que se acaba de mutar. La incertidumbre conserva el Owner y el corte;
su limpieza nunca vuelve a confirmar ni repetir la copia.

Los negativos de activación y las rutas restauradas se cualifican conjuntamente
en el [registro de reconciliación](PHASE_A_RECONCILIATION_CLOSEOUT.md), con decisiones
sintéticas de tests y decisiones GUI delegadas diferenciadas. Sus identidades,
recibos y controles finales se registran allí; las cifras de abajo conservan
su alcance histórico. La continuación acredita copia DELETE de dos notas y copia
WAL de tres notas, con rechazo/cancelación/caducidad, recibos únicos y reinicios.
La prueba de vinculación externa sigue teniendo el límite explícito del registro.

## Contrato del núcleo

`memory_scope_summaries` y `review_memory_scope_copy` inspeccionan imágenes RAM
acotadas, sin abrir la fuente con SQLite, construir stores ni migrar archivos.
Sólo se admiten ámbitos duraderos del mismo agente; un ámbito ligado a sesión
queda excluido sin denominarlo automáticamente memoria temporal. La revisión fija
identidad física de la base, generaciones, huellas, destino vacío y límites.

`MemoryStore.copy_scope` requiere un contexto de un uso que liga operación,
digest de la revisión del host y digest semántico del núcleo. El llamador debe
aportar la autoridad central y mantener el corte del perfil, agente y workspace;
el contexto por sí solo no acredita autorización. Los metadatos internos de ámbito
no deben viajar sin filtrar al cliente ni al modelo.

La copia conserva originales y crea IDs nuevos con revisión 1. Mantiene texto,
clave, título y URI exactos; la URI sigue siendo una etiqueta no verificada y no
se abre. Una sola transacción incluye upgrade v1→v2 cuando proceda, documentos,
chunks/FTS, generación destino, mapa de procedencia y recibo SQL. La lectura y las
operaciones normales de una memoria v1 no la actualizan automáticamente.

La autoridad se comprueba antes de abrir la base fría y de cada efecto, incluido
COMMIT. Cancelación o fallo antes del commit revierten todo el lote, incluido DDL y
versión. Un COMMIT despachado seguido de confirmación perdida conserva `unknown`;
no se repite la copia. Una apertura intentada puede haber creado locks o cambiado
políticas de conexión aunque no se confirmase ninguna copia de ámbito.

La limpieza incierta conserva el propietario y bloquea el uso normal del store.
El host debe retener el mismo corte hasta terminar realmente la limpieza. Ésta
sólo asienta rollback y políticas propias; nunca COMMIT ni una segunda copia.
La consulta del registro SQL observa filas actuales, no prueba un ACK histórico.
Una edición o eliminación legítima puede hacer que los documentos actuales ya
no coincidan, sin invalidar la historia ni convertirla en autorización.

Los límites incluyen texto, documentos, chunks, historia y trabajo SQL. El límite
físico usa `max_page_count` y el tamaño real antes de COMMIT; el host debe derivar
además el presupuesto agregado con las fuentes hermanas y sus límites de filas.
No se truncan ni resumen datos para hacerlos caber. SQLite utiliza la política de
durabilidad comprobada durante la copia; esto no certifica el hardware frente a
cortes eléctricos. Lecturas de sistema bloqueadas no se vuelven interrumpibles
por añadir un timeout de Python.

## Respaldo, diagnóstico y nueva restauración

El inspector acepta exactamente memoria v1 y v2, con esquemas, contenido, historia
y presupuestos validados en RAM. Otros roles mantienen sus versiones anteriores.
Doctor valida que la versión inspeccionada coincida con la cabecera seleccionada.

La historia no tiene dependencias de documentos, ámbitos o agentes que después
pueden cambiar o desaparecer. Cada recibo se valida contra su mapa inmutable:
agente, ámbito duradero distinto, IDs nuevos, ordinales, cantidades, hashes y bytes.
En otra restauración se conserva ese registro como `restored_history`, sin
transformarlo en operación local, permiso o idempotencia. La transformación ocurre
en la copia RAM; no reescribe el respaldo original. Otro perfil sigue necesitando
su propia activación explícita.

## Pruebas y alcance pendiente

El núcleo privado pasó 153 casos: 43 nuevos y 110 de memoria anterior. La
compatibilidad pasó 212 casos con 32 nuevos, incluido un roundtrip producido por
la copia real del núcleo: backup, verificación, nueva restauración, activación de
fixture y resolución de citas. El árbol integrado pasó 494 pruebas focalizadas,
Ruff, bytecode y revisión de diferencias. El checkpoint `f8e5a67` pasó 1829 pruebas
Python, 175 Swift, empaquetado y los gates, con CI aprobada en macOS y Linux
([ejecución 40170656817](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40170656817)).

Los casos cubren UTF-8, IDs y citas, origen intacto, destino con tombstone, cambios
físicos, contextos no prestables, límites, fallo SQL a mitad de lote, SQLITE_FULL,
rollback de DDL/FTS/versión/recibo y confirmación perdida tras COMMIT. También se
comprueban propietario retenido, historial posterior a edición/borrado, lectura
v1 sin migración y rechazo de SQL de esquema sobredimensionado antes de analizarlo.
Todas las decisiones de estos fixtures son sintéticas.

El host pasó 85 casos nuevos y de auditoría independiente en 42,37 s, incluidos
57 del registro de recibos y cuatro de cierre con propietario retenido, más 154
casos anteriores en 19,21 s. Los 239 casos son distintos. Swift pasó 21 casos
headless y dos recorridos Swift→RPC autenticado→SQLite en 2,62 s. En ellos se
comparan hashes independientes del contenido origen/destino, IDs nuevos,
revisión 1, denegación sin cambios y cero llamadas al SDK. Los 38 módulos Python
del recorrido IPC son idénticos a los integrados; no se extrapola una prueba
de fixture a la decisión real del dueño.

La auditoría reprodujo un fallo de custodia nativa: tras perder el ACK de una
consulta, una segunda lectura rechazada podía borrar su estado de espera.
La misma regresión falló tres aserciones antes y pasó después de impedir lecturas
superpuestas. Cerrar Ajustes conserva el efecto, su revisión y cualquier limpieza
pendiente; una consulta SQL nunca reescribe el recibo histórico ni autoriza replay.

El checkpoint `0e383c7` pasó 1914 pruebas Python en 121,14 s y 198 Swift en 24,45 s,
además de Ruff, bytecode, base limpia, paquetes y pre-push. Su
[CI macOS/Linux](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40171847442)
también pasó. La prueba real posterior encontró un defecto de interacción que
esos controles no cubrían; se documenta a continuación.

La copia de dos notas de prueba desde la app quedó completada y asentada con
aprobación real, sin simularla. Sus originales sintéticos tenían revisiones 2 y 1.
En ese checkpoint los negativos y la copia sobre WAL frío seguían pendientes.
La ampliación actual los trata por separado; archivos de workspace y la matriz
finita de recuperación ya tienen evidencia posterior en
[el cierre nativo](PHASE_A_RECOVERY_NATIVE_CLOSEOUT.md). Otros canales y la
distribución autocontenida permanecen fuera de ese recorrido y de este caso feliz.

La investigación estática que orienta estas decisiones es
[OC-17](REA_PART17_SUMMARY.json), con [procedencia](REA_PART17_PROVENANCE.json).
No se importa ni ejecuta código de OpenClaw y sus helpers no examinados siguen
siendo desconocidos.

## Autoridad, corte e interfaz integrados

El panel «Memoria conservada» exige elegir agente y ámbito explícitamente. El
inventario pagina 32 filas, excluye ámbitos ligados a sesión y sólo publica
metadatos: no transmite texto, URI, IDs de documentos ni claves históricas de
workspace. La lista no deduce cuál es el origen más reciente. La revisión y la
aprobación caducan; el destino actual debe seguir vacío.

`memory.restore.copy_scope` usa la autoridad central del host y fija la revisión
semántica del núcleo dentro de sus argumentos canónicos. La apertura fría, la
transacción y COMMIT revalidan esa decisión. Sólo se delega el recurso de memoria
seleccionado al store, conexión e identidad fijados; las fuentes hermanas siguen
congeladas. El contexto de autorización sale en su Task original y la limpieza
desde otra Task conserva exactamente el mismo corte, sin una aprobación nueva.
La generación del perfil avanza una sola vez después del cierre real del corte.

La revisión deriva el límite físico restante del presupuesto agregado de 128 MiB
y comprueba las filas proyectadas de documentos, chunks, FTS e historia contra
el inspector portátil. No ofrece una aprobación si la copia dejaría una base
que respaldo o diagnóstico no pudieran admitir. La consulta de un registro
anterior es independiente de la carpeta de trabajo actual y del ACK original.

La app bloquea conversación, cambio de sesión, reconexión y apertura de otro
perfil mientras conserve una lectura no asentada, una solicitud incierta o un
propietario pendiente. Sus recibos sobreviven al avance de generación; los
previews antiguos se invalidan. Los tests del Store ejercen estas condiciones;
las conexiones concretas de ChatStore fueron revisadas en código, sin presentar
un XCTest del Store como prueba de toda la app.

## Pulsación durante una consulta de estado

El dueño informó que pulsar «Autorizar esta copia» no produjo efecto. La lectura
independiente observó la operación pendiente y después su caducidad, sin recibo
de copia, limpieza pendiente ni cambio del archivo SQLite sintético. No existe
una traza que atribuya causalmente esa pulsación concreta a un punto de código.

Sí se reprodujo un defecto nativo suficiente para descartar una decisión válida:
el sondeo usaba `isRequesting`, deshabilitaba la tarjeta y hacía salir de `decide`
sin enviar un RPC ni explicar el rechazo. Se separó `isRefreshing`, se captura el
ticket mostrado al pulsar y se invalidan las respuestas lectoras anteriores a la
decisión. Los demás controles y la apertura de otro perfil conservan sus barreras.
La caducidad y los rechazos muestran una explicación; no se reenvían decisiones.

Las ocho regresiones nuevas incluyen el clic concurrente, doble clic, respuesta
perdida, ticket obsoleto, caducidad y cierre. Una prueba adicional usa la conexión
serial real: retiene un status, encola la decisión, libera la lectura desde otra
conexión de fixture y comprueba exactamente una aceptación y copia, con contenido
igual y cero llamadas al SDK. Se mantienen las dos pruebas IPC previas.

También se probó la vista de producción con clic físico y transporte falso: el
mismo escenario pasó de cero a una decisión y la respuesta tardía no restauró la
tarjeta. Esa prueba no ejecutó una aprobación ni una copia reales. Un espejo de
botones aisló estilos y zonas de clic sin hallar otro defecto confirmado. Fuentes,
hashes y límites están en [el registro de la regresión](PHASE_A_OWNER_CLICK_REGRESSION.json).
El producto `985b72a` pasó 1914 pruebas Python y 207 Swift, paquetes, smoke de
instalación y ambos hooks. Su [CI macOS/Linux](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40174917627)
también pasó. El backend Python no cambió en ese arreglo.

## Copia autorizada observada en la app

El [registro del recorrido real](PHASE_A_MEMORY_COPY_LIVE.json) conserva la
operación `a81b1205f2a649e69323278e74f4883b`, su digest y la identidad del perfil
sintético. La app mostró «Copia confirmada: 2 notas, 175 bytes»; el servicio
confirmó `complete`, recibo durable y ninguna limpieza pendiente. Una solicitud
anterior había caducado sin efecto; no se reintentó esa operación.

Un verificador independiente de sólo lectura comparó la imagen SQLite con el
oráculo preparado antes de la prueba. Confirmó esquema 2, originales completos
intactos, dos IDs nuevos con revisión 1, contenido y citas exactos, generación
destino 1, un solo recibo local y sus dos filas de procedencia, incluido su hash.
No abrió la fuente mediante SQLite ni realizó otra copia. La consulta nativa
«Consultar registro local» confirmó documentos y generación coincidentes; no
reescribió el recibo ni acreditó el ACK histórico y mantuvo `permits_replay=false`.

Se utilizó un bundle de desarrollo con Python externo y una copia privada de los
38 módulos propios, verificados byte por byte contra el producto. Un arranque
anterior quedó dentro de la inicialización de rutas de CPython, antes de Jarvis;
el archivo concreto y la causa del sistema operativo siguen desconocidos. El
runtime aislado arrancó con las mismas fuentes y cuenta desconectada. Este
aislamiento no acredita una distribución autocontenida ni sustituye la app anterior.

El observador no envió una decisión de autorización. No se instrumentó el gesto
real del dueño; esa evidencia es distinta de la reproducción sintética de la
carrera. El recorrido cierra sólo este caso feliz de copia, sin extrapolarlo a
los demás escenarios de recuperación o a la fase A completa.
