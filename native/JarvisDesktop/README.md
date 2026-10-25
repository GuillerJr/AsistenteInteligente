# Jarvis nativo — cliente de desarrollo

Implementación propia SwiftUI, macOS 14 o posterior, Apple Silicon, sin recursos
ni runtime OpenClaw. Ventana, Dock, barra de menús y atajo global configurable
(⌘⇧J o ⌥Espacio) comparten una instancia del servicio.

Desde la raíz del repositorio:

```bash
./script/test_desktop.sh
./script/build_desktop.sh
```

El segundo comando crea y firma ad hoc un bundle nuevo en `/private/tmp`; no
instala ni abre ninguna app. No es un paquete autónomo de distribución: requiere
Python, las fuentes y el cliente oficial Codex externos. Para una prueba manual
autorizada, usa la ruta del bundle devuelta y cuatro rutas absolutas explícitas:

```bash
open -n /private/tmp/RUTA/Jarvis.app --args \
  --python /RUTA/AL/REPO/.venv/bin/python \
  --source /RUTA/AL/REPO/src \
  --state /private/tmp/ESTADO-NUEVO-PRIVADO \
  --codex /RUTA/AL/CLIENTE-OFICIAL/codex
```

El directorio de estado debe ser nuevo o propio con permisos `0700`, sin enlaces.
No apuntar al estado del Jarvis anterior ni al historial personal. El cliente
inicia `python -m jarvis_core.service --state … --codex …`, supervisa su proceso y
solicita su cierre al salir. La inferencia usa exclusivamente Codex con ChatGPT;
el dueño completa el acceso oficial en su navegador.

El arranque admite hasta 30 segundos para la primera importación del servicio y
consulta su disponibilidad con llamadas breves de solo lectura. Si falla, conserva
el error específico; no lanza otro proceso ni adopta el servicio de otra instancia.
Si el hijo sigue vivo al agotar la espera, indica «Arranque no confirmado» y
continúa sólo con consultas de disponibilidad. Esa recuperación no crea chats ni
repite operaciones; elimina únicamente su propio aviso al recuperar el servicio.
Las llamadas normales tienen un plazo total de hasta 30 segundos, compatible con
el plazo de 20 segundos del cliente oficial. El I/O usa un reloj monotónico y no
renueva el plazo cada vez que llega un fragmento.

Cada RPC usa socket Unix, token privado `0600`, JSON-RPC 2.0 con límite de 1 MiB e
identidad de solicitud comprobada. No hay listener web. Esta autenticación no
acredita identidad firmada del cliente macOS, TCC ni permiso para acciones reales.
Cancelar solo solicita cancelación; el cliente espera un estado terminal antes
de habilitar otra consulta. No se repiten automáticamente mutaciones fallidas.

Las pruebas Swift verifican framing, errores, configuración y conexión real al
servidor Python con estado sintético temporal. También arrancan y cierran la CLI
real tras un retraso sintético de seis segundos y reciben una respuesta RPC que
tarda cuatro segundos, para cubrir el arranque y los plazos del cliente.
No sustituyen la verificación
manual de ventanas, Dock, atajo ni una conversación real con la cuenta del dueño.
La distribución autónoma, notarización y módulos específicos Jarvis permanecen
fuera de esta implementación.

## Herramientas, decisiones y memoria de la fase A

El panel «Herramientas» consulta el catálogo real del servicio y muestra el espacio
privado de Jarvis o la carpeta elegida por el dueño mediante el selector nativo.
No selecciona datos personales automáticamente. Las operaciones pendientes muestran
sus argumentos JSON canónicos exactos, sesión, digest y caducidad mediante la API;
los controles «Permitir esta operación» y «Rechazar» sólo transmiten la decisión
humana ligada a esa tarjeta. Revocar invalida autorizaciones aún no despachadas.
Los recibos de herramientas se presentan aparte del texto del modelo, con estados
de comprobación y resultados inciertos, y señalan las vistas parciales del output.

El panel «Memoria» distingue el espacio privado, la carpeta elegida y los temporales
por conversación. Busca texto local y conserva fuentes/citas del snapshot. La
comprobación de una cita llama al servidor y coteja revisión, hash y bytes; no abre
una URL ni acredita la verdad o actualidad de una fuente externa. Una búsqueda
léxica no se presenta como memoria semántica. Los temporales se descartan al cerrar
el servicio y no se muestran al cambiar de conversación.

La barra lateral permite renombrar, archivar/restaurar y borrar un registro local
con confirmación. Los borradores se conservan por conversación en RAM. Una petición
sin recibo conserva su identidad; consultar/reconectar no vuelve a enviar una
mutación. Al recuperar un run activo se retoma su supervisión. Si un temporal se
pierde al reiniciar, la interfaz conserva el aviso de resultado sin confirmar hasta
que el dueño lo reconoce; no lo transforma en éxito.

La conversación seleccionada también se actualiza por lecturas acotadas cuando
un canal externo añade mensajes o una ejecución. Los IDs persistentes evitan
reconstruir mensajes iguales; tampoco se reasignan recibos idénticos. Se descartan
respuestas y errores que lleguen después de cambiar de scope, sin solapar lecturas.
Un run externo pendiente se sigue para mostrar su tarjeta de aprobación. No se
reenvían mensajes, y el refresco conserva el borrador y la voz en curso.

Las pruebas incluyen DTOs de scope/digest/caducidad, un entero JSON mayor que 2^53
conservado sin reserializar, citas/offsets/hash y recibos. Los intercambios de
aprobación/cancelación y citas obsoletas usan un socket Python real con un peer de
contrato sintético: no certifican una acción productiva ni sustituyen al dueño.

## Gestión MCP stdio

El panel «MCP» muestra configuraciones y conexiones reales mediante la API local.
El dueño elige un ejecutable con el selector nativo, escribe sus argumentos como
array JSON y selecciona una de las versiones del protocolo que publica el servicio.
Guardar no ejecuta ni conecta; tampoco hay conexiones automáticas al abrir Jarvis.
«Conectar…» exige revisar un array JSON completo en una confirmación nativa. Los
caracteres de control y Unicode se muestran escapados para revisar cada argumento.
La API recibe el mismo programa/argv y protocolo, y rechaza configuraciones cambiadas
antes del lanzamiento. Desconectar y quitar actualizan el catálogo de herramientas.
Las ejecuciones activas bloquean cambios para no redirigir operaciones pendientes.

Un programa MCP se ejecuta con el usuario del dueño. Su HOME privado separa
configuración y no constituye una sandbox del sistema. Configurarlo o conectarlo
no autoriza efectos de sus herramientas: éstas usan la misma autoridad y tarjetas
de aprobación por operación, sesión, run y digest. Los errores del servidor se
muestran saneados; una interrupción no provoca reconexiones ni mutaciones repetidas.
Las pruebas usan un peer de contrato sintético y no certifican servidores externos.

Para pruebas de desarrollo se admite además `--provider-state /RUTA/EXPLICITA`:
permite usar la conexión oficial ya autorizada del perfil de pruebas junto a un
`--state` nuevo sin importar conversaciones. El cliente sólo pasa la ruta al
servicio; no busca, copia ni lee credenciales. Sin ese flag conserva el directorio
de proveedor predeterminado del perfil seleccionado.

## Gestión de la base A

El menú «Gestionar» abre agentes, skills, programador, procesos y Telegram. El agente se
elige para conversaciones nuevas; una conversación existente conserva su agente.
«Ramificar conversación» copia sólo contexto completado a un chat nuevo, sin
runs, recibos, credenciales ni aprobaciones. Las tareas pueden abrir su propio
chat desde el historial para que el dueño vea y decida sus operaciones.

La gestión usa un canal RPC separado de conversación, decisiones y MCP. Agentes,
importación/edición/activación de skills, cambios de tareas y activación/detención
del programador requieren revisión humana nativa de los parámetros exactos. La
app no pulsa esas decisiones. Las revisiones y hashes protegen los snapshots
frente a cambios entre lectura y confirmación; un fallo no reenvía una mutación.

Una skill se importa de una carpeta escogida mediante NSOpenPanel, tras mostrar
el texto de SKILL.md y cotejar su SHA-256 localmente. Se guarda deshabilitada. Los
recursos son sólo metadatos y no se leen ni ejecutan. Habilitarla muestra la
versión actual y conserva la autoridad central para cualquier acción posterior.

Las tareas se crean deshabilitadas. El programador se activa expresamente tras
revisar el catálogo completo ligado a su hash; no arranca al listar ni al abrir
la app. Una regla es una fecha UTC, un intervalo o una hora diaria con zona IANA.
La vista indica carpeta vinculada, agente, próxima ocurrencia e historial; un
resultado incierto conserva ese estado. No se concede aprobación por programar.

La vista de procesos sólo lista y consulta el ámbito de la conversación actual,
con backend de aislamiento, código de salida, salida paginada y hash del flujo.
Un proceso con exit 0 no acredita efectos físicos. Pedir su detención prepara un
borrador para la herramienta process_stop: el dueño todavía debe enviarlo y
aprobar la tarjeta normal. No existe una ruta RPC de gestión que evite esa
aprobación. Cambiar de conversación descarta las salidas de su ámbito anterior.

Las pruebas nuevas ejercitan el servicio Python/SQLite/sockets reales con
proveedor, prompts y decisiones sintéticos. Cubren ausencia de despacho implícito,
versiones/hash obsoletos, activación explícita de una tarea, enlace a su sesión,
ramificación, aislamiento de DTO y resultados de procesos sin falsa verificación.

## Telegram y enlace privado

El panel «Telegram» sólo conecta un bot cuando lo decide el dueño. El token se
introduce en un SecureField, se borra del campo al conectar o cerrar el panel y
sólo se transmite como argumento transitorio al servicio. No se guarda en
preferencias, revisiones JSON, logs propios ni mensajes del modelo. El servicio
lo conserva en RAM hasta desconectar o cerrar; debe introducirse de nuevo tras
reiniciar. No se conecta ni activa un canal al abrir Jarvis o consultar su estado.

Cada ruta autoriza un chat privado numérico positivo y crea una conversación
persistente propia con el agente elegido. Se revisan bot, rutas, sesiones, carpeta
e identidad de configuración antes de activar el canal por separado. Los grupos y
adjuntos no entran en esta ruta de texto. Los mensajes de Telegram nunca resuelven
las tarjetas de autorización nativas de herramientas. Los registros del canal
presentan sólo metadatos y conservan los resultados sin confirmar sin reenvíos.

«Enlazar tu chat desde Telegram» evita introducir el identificador manualmente.
El dueño elige agente y revisa su versión, bot y carpeta, con dos confirmaciones
expresas: leer/avanzar las actualizaciones del bot, incluidas las ignoradas, y usar
un bot sin otro consumidor. El bloqueo del servicio sólo excluye lectores locales.
El intento caduca en un máximo de 180 segundos y no ejecuta modelos ni envía una
respuesta. El dueño pulsa un botón fijo para abrir el bot en la app Telegram y,
si hace falta, pulsa «Iniciar» allí. Jarvis no inspecciona sus chats.

La URI privada se obtiene una sola vez y pasa directamente al adaptador nativo.
Se valida exactamente el [formato oficial de enlaces de bot](https://core.telegram.org/api/links#bot-links).
No se muestra ni copia, no entra en la accesibilidad de la vista ni en su texto,
ayuda, historial, preferencias o logs propios. Sólo se abre en una aplicación
Telegram reconocida por sus metadatos de bundle, mediante una URL `tg:`; esos
metadatos no acreditan firma. Se desactivan Recientes y la sustitución de otra
instalación, sin usar navegador ni un handler arbitrario. La apertura tiene un
plazo de 30 segundos y descarta diagnósticos que podrían incluir la URI. Si falla,
se cancela el lector; si ese cierre no se confirma, se conserva la incertidumbre
y se pide una nueva cancelación explícita, sin reintentar el enlace.

La coincidencia únicamente propone un chat. Una segunda revisión nativa del
dueño, ligada a claim, agente y revisión exactos, crea su ruta y conversación.
El canal continúa detenido hasta activarlo por separado. Cancelar, caducar o
desconectar descarta el intento. Telegram conserva el mensaje `/start`; la garantía
de no incorporar su código a mensajes/modelo/journal corresponde a Jarvis, y no
promete borrar registros externos de Telegram o del sistema.

## Voz local

«Dictar» solicita los permisos de micrófono y reconocimiento sólo tras la decisión
del dueño. Exige reconocimiento en este Mac y no ofrece un respaldo remoto. Si el
idioma o su modelo local no están disponibles, muestra un error controlado. No
guarda archivos de audio ni transporta PCM al servicio Python, Codex u otro
proveedor. El controlador no consulta permisos, hardware o voces al construirse.

La captura se limita a 30 segundos, la espera de permisos a 30 y la finalización a
3. El texto final o parcial sólo se añade al borrador de la conversación y modo
temporal/persistente originales, conserva lo escrito y señala que debe revisarse.
No envía un mensaje automáticamente. Se bloquea el envío durante el dictado y se
cancela la voz al cambiar de contexto, desconectar, cerrar la ventana o terminar.

«Leer en voz alta» en una respuesta usa una voz Apple instalada del idioma elegido,
con texto y duración limitados. No descarga voces ni usa Personal Voice; tampoco
cambia a otro proveedor cuando falla. El dueño puede detener la lectura. La app incluye
las dos descripciones de privacidad necesarias; no concede permisos TCC por él.
Las pruebas con driver sintético y reloj controlado acreditan el ciclo de vida y
los límites, no la exactitud del dictado ni que un altavoz haya sido escuchado.

La suite nativa incluye 97 pruebas de framing, gestión, voz y contratos Telegram,
incluido pairing con servicio/journal propios y Bot API/opener sintéticos. La
validación privada previa utiliza una identidad de fuentes declarada y debe
repetirse sobre el árbol integrado antes de publicar. No abre cuentas, Telegram,
micrófono ni permisos reales. Las comprobaciones físicas con el idioma, bot,
dispositivos y autorizaciones del dueño son recorridos separados.
