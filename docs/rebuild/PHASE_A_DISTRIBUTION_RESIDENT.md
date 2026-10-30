# Distribución autocontenida y servicio residente

Incremento solicitado el 30-10-2026, dentro de la fase A. El código está en
cualificación; las pruebas y el bundle completos se ejecutan exclusivamente en
CI remota. No se sustituye la app instalada ni se abre el perfil anterior.

## Qué se distribuye

La app arm64 incorpora un intérprete CPython independiente, el código propio de
`jarvis_core`, todas las dependencias productivas fijadas en `uv.lock`, el paquete
oficial completo de Codex, certificados CA y los activos de MiniLM CPU ya elegidos.
No necesita el repositorio, `.venv`, Homebrew, un Python del sistema, Node ni un
Codex externo. macOS y sus frameworks siguen siendo requisitos del sistema.
Codex con ChatGPT sigue requiriendo Internet y el inicio de sesión oficial del
dueño; llevar el cliente no convierte la inferencia en local.

Los pins están en `script/runtime_artifacts.json`: CPython 3.11.18 de
python-build-standalone 20261026 y Codex 0.163.0 para aarch64-apple-darwin. Se
conserva el paquete de Codex completo, incluido su host auxiliar y sus recursos;
no se extrae código de la app de Codex. Las licencias publicadas se incluyen junto
al runtime y cada componente lleva origen, versión y huella del archivo adquirido.
La licencia de Jarvis se conserva. OpenClaw no forma parte del paquete.

El ensamblador sólo admite el SHA exacto del checkout limpio en CI y un destino
nuevo dentro del presupuesto de compilación. Descarga archivos con tamaño y
SHA-256 fijados, resuelve exclusivamente wheels compatibles del lock, no ejecuta
hooks de instalación y materializa enlaces internos seguros como archivos
ordinarios. Rechaza escapes, colisiones, ciclos, archivos especiales, dependencias
Mach-O externas al runtime —salvo frameworks/librerías de macOS— y versiones
inesperadas. Firma el código anidado antes de generar el manifiesto final.

`Contents/Resources/JarvisRuntime/runtime-manifest.json` identifica cada archivo,
tamaño, SHA y condición ejecutable. El hash del manifiesto queda en el Info.plist
firmado de la app. El host nativo verifica firma, árbol exacto y hashes antes de
iniciar Python y de nuevo antes de recuperar un servicio o registrar el arranque.
El servicio vuelve a verificar su runtime antes de abrir el perfil. Un runtime
ausente o alterado no activa una búsqueda en PATH ni una vuelta al modo de desarrollo.
El cálculo de hashes se hace fuera del actor de interfaz y se une al cancelar.

El modo producto no admite argumentos externos para sustituir Python, código,
dependencias, Codex o perfil. Crea un perfil nuevo en
`Application Support/Jarvis Desktop/Perfiles/Principal`, separado de los anteriores.
HOME y temporales del servicio se limitan a carpetas privadas de ese perfil. La
CA procede de bytes verificados, no de variables ambientales ni rutas de Homebrew.
Codex recibe una ruta CA explícita y su propio HOME/CODEX_HOME privados.

MiniLM se prepara mediante la operación aprobada existente. En esta distribución
se copian sólo los activos incluidos, sin red ni fallback; el destino conserva
permisos 0700/0600 y publicación exclusiva. No se relaja la inspección privada de
los modelos ni se descarga durante una búsqueda. La reparación usa el mismo
origen incluido y conserva el original.

## Residencia y arranque

El residente es la app de la sesión del usuario, con acceso desde la barra de
menús. Cerrar sus ventanas conserva el servicio, las tareas ya autorizadas y sus
controles. El arranque pertenece al delegado de aplicación y no depende de que
una ventana aparezca. «Salir de Jarvis» cierra el trabajo y el servicio; si el
cierre es incierto conserva la custodia y lo comunica.

«Servicio y arranque» permite revisar el estado real de `SMAppService.mainApp`
y confirmar el registro o retirada del inicio al entrar en macOS. Distingue
desactivado, activado, pendiente de aprobación del sistema y estado desconocido.
Un registro solicitado no equivale a estar activado. Una revocación externa no
se deshace automáticamente y las operaciones vuelven a leer el estado del sistema.
No cambia esta preferencia al instalar, abrir Ajustes o iniciar un perfil.

El servicio Python sigue siendo hijo del host nativo. Un pipe privado, cuyo
extremo escritor conserva exclusivamente el host, permite detectar su muerte:
EOF solicita el cierre normal de RPC, Codex, MCP, tareas y stores. No usa un PID
como autoridad, no manda señales al padre ni reproduce acciones. Si el padre ya
terminó antes de admitir el servicio, no se abren los stores. La recuperación
del hijo mantiene su límite previo y espera que se libere el lease.

Esta arquitectura no promete reabrir la app después de «Salir» ni relanzarla
automáticamente si se cae el propio host. No instala un daemon de root ni un
segundo backend sin el broker nativo. El inicio al entrar es optativo y queda bajo
control del dueño en los ajustes de macOS.

## Cualificación y límites

`script/build_desktop.sh --full --self-contained` se usa sólo en CI, con el modelo
público fijado admitido por el supervisor. El ensamblado y la prueba de traslado
comparten un límite remoto de 8 GiB y 1.200 segundos. No se amplía el presupuesto
local ni se compila en el Mac del dueño.

`script/qualify_bundled_runtime.py` traslada la app a otra ruta y arranca el Python
incluido, en modo aislado, con PATH del sistema y un HOME sintético. Comprueba
librerías nativas, Opus, CA, MiniLM real CPU, el protocolo del Codex oficial con
una cuenta vacía, IPC firmado, SQLite y cierre por EOF con lease libre. No conecta
cuentas, no abre el micrófono y no registra Login Items. El recibo saneado queda
en `Contents/Resources/JarvisRuntimeQualification.json`, fuera del runtime; la
firma exterior se aplica después. La procedencia de CI identifica el commit,
archivo, hash del manifiesto y límites de esa prueba.

La cualificación nativa posterior usa una copia con identidad `local.jarvis.qa.*`
y un perfil sintético en `/private/tmp`, fijado en un JSON firmado y con hash en
Info.plist. Tampoco admite argumentos de runtime. Ese modo deshabilita el registro
real de Login Items: una aprobación de datos de prueba no cambia preferencias
persistentes del Mac. No se reutilizan credenciales del dueño ni se leen tokens.

La firma ad hoc de CI acredita integridad de esta copia y ejecución de desarrollo;
no es Developer ID ni notarización. El dueño indicó que no tiene membresía Apple
Developer. La distribución pública notarizada y su aceptación por Gatekeeper en
un Mac ajeno siguen requiriendo esa decisión y credenciales. Tampoco se presenta
una prueba en macOS 27 como prueba de todas las versiones desde macOS 14.

Este incremento no completa la matriz amplia de fase A ni inicia la fase B.
Actualización pública, instalador firmado, todos los canales/nodos y plataformas
no elegidas mantienen sus criterios separados.

## Referencia y trazabilidad

Se conservan los recorridos REA OC-15/20/21A y la referencia inmutable OpenClaw
`1e3ff306abaff29f03e7668d53b01c07f10294ac`. El contraste de fuentes ordinarias
E009 (`src/daemon/service.ts`, SHA e375a69b88621be86456f1fa417fed540e72bae7c425276066d0b39fa72df88c)
y E010 (`src/daemon/launchd.ts`, SHA a02640c5ea5fb68d2b1290708a62fff575897e1559bba740a93a590dc784039c)
informa estados de instalación, comprobación de identidad y límites de recuperación.
No es un nuevo lote REA, ejecución de OpenClaw ni revisión exhaustiva del árbol.

Fuentes primarias: [SMAppService.register](https://developer.apple.com/documentation/servicemanagement/smappservice/register()),
[distribuciones de python-build-standalone](https://gregoryszorc.com/docs/python-build-standalone/main/distributions.html)
y [app-server oficial de Codex](https://learn.chatgpt.com/docs/app-server).
