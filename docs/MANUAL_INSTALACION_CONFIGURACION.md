# Manual de Jarvis

Esta es la fuente única de instalación, primeros pasos y uso cotidiano. `MANUAL_FACIL.md`
apunta a este mismo documento y la app incluye una copia en Ayuda › Manual de Jarvis.

## Qué necesitas

- Mac Apple Silicon con macOS 14 o posterior.
- Conexión a internet, Codex instalado y una cuenta de ChatGPT con acceso a Codex.
- Para compilar desde código: Git, Xcode completo con Swift 6.2 o posterior, Python 3.11
  y `uv`. Usa las herramientas nativas arm64; compruébalo con `uname -m`.

Jarvis usa Codex con ChatGPT para la inferencia. El inicio de sesión se completa
en el navegador oficial. Si la conexión no está disponible, informa el problema;
no cambia de proveedor automáticamente. La salida de voz y las notas de audio de
Telegram están sin configurar tras retirar NIM. Las herramientas nativas y sus
aprobaciones siguen ejecutándose en el Mac.

La búsqueda semántica obtiene embeddings localmente con el encoder de Apple.
Los recuerdos y el índice permanecen en el Mac. Los fragmentos de contexto que una
petición autorice sí pueden enviarse a Codex para preparar la respuesta; la redacción
de credenciales no anonimiza todo el texto personal. Los chats temporales no consultan esa memoria. Si la búsqueda
semántica falla, se conserva la búsqueda local por palabras clave. Diagnóstico indica
si el índice está preparado, indexándose o temporalmente degradado.

## Instalar desde código

La distribución pública necesita una firma Developer ID y notarización. Mientras
se prepara, el repositorio permite instalar una compilación con firma local estable.
No desactives Gatekeeper ni las comprobaciones de firma para compartirla.

```bash
git clone https://github.com/GuillerJr/AsistenteInteligente.git
cd AsistenteInteligente
uv sync --all-groups --no-editable --python 3.11
./script/local_codesign_identity.sh install
./script/setup_git_gates.sh
.venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/python -m pytest -p no:warnings
./script/test_native.sh
./script/menu_bar_service.sh install
```

Si el proyecto vive en iCloud, utiliza el entorno `.venv.nosync` al que apunta `.venv`
y omite los duplicados que iCloud llama `* 2.*`. No agregues claves ni el entorno a Git.

El instalador compila, firma e instala `~/Applications/Jarvis.app`. También se admite
mover la aplicación completa a `/Applications`: sus helpers y recursos se resuelven
con relación al bundle. No arranques directamente el ejecutable dentro del bundle.
La app administra su daemon Python. Para el inicio automático utiliza el interruptor
de Ajustes › General; no instales un segundo LaunchAgent del daemon.

Las contraseñas del llavero las escribe su dueño en la ventana de Terminal o en el
diálogo de macOS que las solicite. No se pasan en comandos, chats ni archivos.

## La primera ejecución

1. Abre Jarvis desde Aplicaciones o Spotlight. La bienvenida se muestra la primera vez
   y cuando no hay una sesión de Codex conectada.
2. En **Ajustes › Modelos**, pulsa **Iniciar sesión con ChatGPT** y después
   **Abrir inicio de sesión**. Completa tú el acceso y consentimiento en el navegador.
   Vuelve a Jarvis, elige un modelo disponible y pulsa **Probar conexión**. La prueba
   usa texto sintético, sin conversaciones ni documentos personales.
3. Revisa cada permiso opcional. Puedes continuar por escrito sin concederlos.
4. Elige si quieres abrir Jarvis al iniciar sesión. Si macOS lo solicita, aprueba el
   ítem de inicio en Ajustes del Sistema.
5. Envía el mensaje de ejemplo o abre el chat para escribir tu propia pregunta.

Puedes **Omitir por ahora** y volver desde **Ajustes › General › Abrir bienvenida**.
La conexión y el modelo se cambian desde **Ajustes › Modelos de IA**. El cambio de
modelo se aplica a las siguientes peticiones y se bloquea mientras haya tareas activas.
Las integraciones conservan **Aplicar y reiniciar** cuando sea necesario.

## Permisos opcionales

| Permiso | Para qué se utiliza |
| --- | --- |
| Micrófono | Escuchar un turno iniciado por ti o la activación por voz que hayas habilitado. |
| Reconocimiento de voz | Convertir voz en texto con las APIs de macOS. |
| Calendario | Consultar agenda para avisos y acciones que solicites. |
| Contactos | Consultar contactos para las acciones que solicites. |
| Automatización | Usar cada aplicación autorizada; macOS pregunta al primer uso. |
| Accesibilidad | Manipular controles y ventanas al usar el Mac. |
| Grabación de pantalla | Ver la pantalla durante las acciones autorizadas de uso del Mac. |
| Red local | Descubrir o conectar dispositivos configurados en tu red. |
| Notificaciones | Mostrar avisos y revisiones de tareas programadas. |

La bienvenida solo muestra Accesibilidad y Grabación de pantalla al elegir el uso
del Mac. Los permisos se conceden y revocan en Ajustes del Sistema › Privacidad y
seguridad. Rechazar un permiso no debe impedir conversar por texto. Si una función
lo necesita, Jarvis debe explicar qué falta. Las pruebas en una cuenta limpia siguen
siendo parte de la certificación de la versión.

## Conversar y navegar

Abre **Conversación › Abrir chat**. Escribe un mensaje o añade un documento o imagen.
El chat conserva su historial cifrado; un chat temporal no aparece en la lista ni
crea recuerdos y se elimina al salir. Los proyectos agrupan conversaciones y contexto.
El **modo plan** permite revisar los pasos antes de implementarlos.

El menú Conversación ofrece Nuevo chat (⌘N), Chat temporal, Modo plan, Buscar chats
(⌘F), Abrir navegador, Archivar y Eliminar. Editar un mensaje o regenerar una respuesta
crea una continuación desde ese punto; revisa de nuevo cualquier acción propuesta.
Puedes valorar las respuestas con los controles de opinión. Eliminar mueve un chat
a la papelera; la eliminación definitiva requiere su acción específica.

**Buscar chats** encuentra títulos y texto de los mensajes. Las coincidencias en
mensajes muestran un fragmento y abren el turno encontrado, aunque sea antiguo. Si la búsqueda es
grande, usa **Seguir buscando** para continuar. Los filtros de archivados y papelera
se mantienen; los chats temporales no aparecen. Al abrir un mensaje antiguo, pulsa
**Volver a recientes** antes de continuar la conversación.

La barra del navegador admite una URL, un dominio o texto libre. Pulsa Intro para
navegar; el texto libre se busca en DuckDuckGo. **Preguntar a Jarvis sobre esta página**
prepara su texto como adjunto. Las instrucciones dentro de una página no conceden
permisos ni aprueban herramientas.

Puedes abrir hasta ocho pestañas con **Nueva pestaña** o ⌘T mientras el navegador
está visible. Ctrl-Tab y Ctrl-Mayús-Tab cambian de pestaña; cada una conserva su
propia navegación. Ciérralas con su botón o menú. Al ocultar el navegador se
conservan durante esa ventana; al cerrar la ventana se descartan.

**Conversación › Historial de acciones** (⌥⌘H), también disponible desde el panel
de la barra de menús, muestra las herramientas usadas, la fecha y su estado desde
esta versión. Puedes filtrar una conversación, eliminar una entrada o vaciar el
historial con confirmación. Se conservan hasta 1.000 entradas y se excluyen los
chats temporales. Borrar entradas no deshace acciones ni elimina los registros
técnicos de seguridad. Un éxito informado por una herramienta no siempre verifica
el efecto externo.

## Voz y acciones

La salida hablada está sin configurar en esta migración. El chat por escrito y
las acciones aprobadas no necesitan una voz configurada.

El atajo configurado en Ajustes › General inicia un turno de voz. También puedes
mantenerlo pulsado y soltarlo para terminar. ⌃⇧J abre una pregunta rápida por escrito.
La activación con «Jarvis» y la identidad de hablante se configuran en Ajustes › Voz.
El entrenamiento y la certificación física se hacen con la voz del dueño.

Las acciones que escriben, envían, borran o cambian algo presentan una revisión.
Solo el dueño puede aprobarlas. En el uso del Mac, los permisos por aplicación se
pueden limitar a la conversación o conservarse cuando la política permita «Siempre».
Puedes revocarlos en Ajustes › Memoria y privacidad. Las aplicaciones sensibles
mantienen sus restricciones aunque exista una concesión previa.

## Tareas programadas y Dot

Crea o modifica tareas desde la sección Tareas del chat. Revisa la hora, zona,
frecuencia, canal de aviso y estado activo. «Ejecutar ahora» permite comprobar una
tarea sin esperar a su siguiente horario.

Si necesita aprobación, el trabajo queda pendiente durante el plazo mostrado y
recibes un aviso **Revisar**. Abrir el aviso solo muestra la revisión: no ejecuta ni
aprueba la acción. Si no respondes antes del vencimiento, Jarvis cancela esa ejecución.
Los avisos por Telegram solo se envían si ya configuraste ese canal.

Dot mantiene objetivos y atención pendiente. Puedes detener, reanudar o borrar un
objetivo desde la interfaz. Revisa las decisiones que solicite antes de continuar.

## Integraciones y extensiones

Ajustes agrupa cuentas, modelos, Telegram, extensiones, dispositivos y privacidad.
Conecta únicamente las cuentas y herramientas que quieras usar. Los permisos OAuth,
las contraseñas, el emparejamiento y las confirmaciones los completa el dueño.
Los complementos importados se revisan antes de instalarse; su contenido no amplía
los permisos del broker. Si una integración necesita reiniciar, la interfaz lo indica.

Las herramientas avanzadas y el contrato de extensiones se conservan en la
[referencia técnica](https://github.com/GuillerJr/AsistenteInteligente/blob/main/docs/REFERENCIA_TECNICA.md)
y en la [guía del CLI](https://github.com/GuillerJr/AsistenteInteligente/blob/main/docs/ENGINEERING_CLI.md).
Las secciones históricas sobre cerebro híbrido no describen la inferencia actual.

## Actualizaciones y versión

Consulta la versión, build, revisión y licencias en **Acerca de Jarvis**. Ajustes ›
General ofrece la comprobación de actualizaciones. El canal debe disponer de un
manifiesto firmado, una raíz de confianza y un paquete publicado. La instalación
requiere que revises y confirmes la versión propuesta; una firma inválida o un
downgrade se rechazan. Si el canal no está configurado, la app lo explica.

La publicación de la primera actualización notarizada depende del certificado
Developer ID y de las credenciales del dueño. La cadena de publicación está en la
[calificación P14](https://github.com/GuillerJr/AsistenteInteligente/blob/main/docs/quality/P14_SECURE_UPDATE_QUALIFICATION.md).

Para actualizar una instalación de desarrollo desde `main`:

```bash
git pull --ff-only
uv sync --all-groups --no-editable --python 3.11
./script/menu_bar_service.sh install
./script/jarvis_beta.sh check
```

## Diagnóstico y datos

Revisa Ajustes › Diagnóstico y el estado de Modelos de IA. Si Codex falla, comprueba
la sesión y prueba la conexión desde la interfaz antes de usar **Reintentar**. Un fallo de conexión
no habilita otros proveedores ni ejecuta una herramienta por su cuenta.

```bash
./script/jarvis_beta.sh check
./script/menu_bar_service.sh status
./script/setup_git_gates.sh --check
```

Los datos privados viven en `~/Library/Application Support/Aegis`; las claves viven
en el llavero. No publiques esa carpeta, el historial, credenciales o capturas que
los muestren. No borres los datos para reparar un fallo sin revisar antes qué contienen.

El pre-commit ejecuta lint y pruebas. El pre-push valida el árbol de trabajo completo,
compila, prueba y empaqueta: termina y confirma todos los cambios antes de subir y
no edites ni instales mientras corre. Los fallos de capacidad de runners se tratan
según la [guía de CI](https://github.com/GuillerJr/AsistenteInteligente/blob/main/docs/quality/CI_CAPACITY.md).

Para el estado de pruebas, limitaciones y requisitos pendientes de publicación,
consulta [V1_PENDIENTES.md](https://github.com/GuillerJr/AsistenteInteligente/blob/main/docs/V1_PENDIENTES.md).
