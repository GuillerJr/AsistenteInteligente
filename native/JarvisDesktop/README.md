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
