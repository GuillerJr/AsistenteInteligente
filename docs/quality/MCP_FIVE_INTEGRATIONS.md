# Cinco integraciones MCP: implementación y estado real

Fecha: 2026-09-16. Fase experimental; no modifica el alcance congelado de Jarvis V1.
Estos conectores pertenecen a Jarvis, no a los plugins de Codex.

## Estado

| Integración | Implementado | Validación | Pendiente antes de uso personal |
| --- | --- | --- | --- |
| GitHub | Política positiva para consultar archivos, commits, issues y PRs; repositorios exactos; doble modo solo lectura | Pruebas de aislamiento de repositorio, método y credencial | Elegir repositorios, instalar/revisar el servidor oficial y autorizar credencial de lectura |
| Google Workspace | Servidor comunitario `workspace-mcp==1.26.1` instalado con lockfile; 12 esquemas reales revisados para Gmail, Calendar y Drive; proyecto OAuth separado con las tres APIs y cliente web | Lecturas de prueba reales en Gmail, Calendar y Drive con aprobación de un solo uso; OAuth en Producción; nuevo refresh token con los mismos 13 scopes validado contra Google; daemon instalado confirma 12 herramientas activas | Probar escrituras concretas cuando el usuario las solicite; verificación de Google si se distribuye la app |
| Filesystem | Servidor de referencia instalado; cinco herramientas de lectura; Documentos y Descargas preparados | Llamada real sobre archivo sintético, confirmación de un uso y descubrimiento del catálogo con las raíces elegidas | Activar el perfil revisado en un daemon actualizado |
| Playwright | Servidor oficial instalado; navegación, instantánea y cierre en navegador aislado | Arranque real y snapshot de navegador vacío, sin navegación de red ni sesiones personales | Elegir orígenes HTTPS y activar su perfil en un daemon actualizado |
| Home Assistant | Cliente MCP Streamable HTTP autenticado para LAN, JSON/SSE, sesiones y revisión de catálogo por llamada; solo `GetLiveContext` | Transporte simulado, control de credenciales, cambios de esquema, redirecciones y respuestas inválidas | Disponer de Home Assistant, comprobar compatibilidad de dispositivos y configurar dirección/token/exposición |

Google Workspace se autorizó y se activó en la app instalada. Las tres lecturas de prueba
devolvieron resultados vacíos; no se abrió contenido de correos ni archivos y no hubo escrituras.
El 16 de septiembre se confirmó el estado OAuth «En producción» y se completó nuevamente el
consentimiento personal. Se comprobó que el refresh token cambió, conserva los mismos 13 scopes,
permanece en un archivo 0600 y permite renovar el acceso ante Google. El daemon reporta una
configuración válida y las 12 herramientas activas. Los perfiles privados están bajo `.aegis/`, fuera de Git y con permisos
0600; Filesystem y Playwright mantienen `enabled: false`. Google Workspace tiene un perfil activo
separado y 12 herramientas disponibles en `mcp-status`. Filesystem, Playwright y Google tienen
huellas de esquema revisadas; GitHub y Home Assistant aún no tienen herramientas aprobadas.
El perfil de Playwright
carece deliberadamente de orígenes/argumentos completos y no puede arrancar hasta configurarlos.
La carpeta de trabajo del navegador es independiente de Documentos y Descargas.

## Límites que no pueden quitar los modelos

- Toda llamada exige aprobación de un solo uso. Los argumentos se validan antes de mostrar
  la confirmación y de nuevo inmediatamente antes de ejecutar. Router y sintetizador no reciben MCP.
- GitHub exige `--read-only` también en el servidor oficial. No se exponen búsquedas globales,
  GraphQL ni herramientas de escritura. La credencial debe tener permisos mínimos; Jarvis no
  puede demostrar los permisos reales de un token a partir de su texto.
- Google utiliza capacidades distintas para lectura y escritura. La huella incluye el esquema
  upstream completo y sus valores predeterminados, aunque Jarvis solo expone los campos permitidos.
  Un parámetro nuevo, una modificación de un valor predeterminado o un esquema incompatible
  exige nueva revisión. Las descripciones externas se eliminan del esquema ofrecido al modelo.
  El lanzador fija `stdio`, `gmail:drafts`, `calendar:full`, `drive:full` y el nivel `extended`.
  Sus ficheros de política y lógica, metadatos de paquete, ejecutable y lockfile tienen huellas
  locales; la carpeta OAuth y los logs son privados. Google guarda el refresh token en un JSON
  local con permisos del propietario, según la implementación comunitaria; no se afirma cifrado
  de esos tokens en reposo. El callback OAuth solo escucha en `127.0.0.1:18731` y no cambia a
  otro puerto si está ocupado.
- Gmail solo permite leer y crear borradores de texto sin firma implícita, adjuntos ni envío.
  Calendar admite
  crear/actualizar con `send_updates: "none"`; no borrar, cancelar, RSVP, invitados ni recurrencias.
  Drive permite metadatos seleccionados y añadir texto al principio/final, no reemplazar/vaciar,
  trasladar a la papelera, mover de carpeta, compartir o modificar permisos. No se ofrecen
  operaciones batch ni importación de archivos/URLs. Docs/Sheets/Slides completos no están incluidos.
  Los scopes OAuth del peer son más amplios que las operaciones que Jarvis admite: la prohibición
  de borrar y enviar se aplica en Jarvis y no equivale a revocar esas capacidades al token.
  En esta instalación, Google Auth Platform contiene 13 scopes: identidad (`openid`, email y
  perfil), Gmail (`readonly`, `labels`, `modify`, `compose`), Calendar (`readonly`, `calendar`,
  `calendar.events`) y Drive (`readonly`, `drive`, `drive.file`). El scope `calendar` permite
  incluso compartir y borrar calendarios; `drive` permite borrar archivos; `gmail.modify` y
  `gmail.compose` permiten enviar mensajes según el texto de consentimiento de Google.
  Estas capacidades del token exceden deliberadamente las herramientas admitidas por Jarvis.
- Filesystem no expone escritura, borrado, movimiento, búsqueda recursiva ni árbol de directorios.
  Bloquea escapes de raíz, rutas relativas, enlaces simbólicos, archivos especiales, archivos
  ocultos y nombres/extensiones comunes de credenciales. Las lecturas exigen archivos de un solo
  enlace de hasta 1 MiB, con hasta diez rutas por llamada; el límite MCP de salida es adicional.
  Esto no detecta todos los secretos posibles dentro de un documento de nombre corriente.
- Playwright exige el lanzador exacto: `--isolated`, `--headless`, `--sandbox`, bloqueo de service
  workers, sin generación de código ni imágenes. No hay clicks, formularios, JavaScript libre,
  cargas, cookies, perfiles existentes, extensión, CDP, permisos de cámara o micrófono.
  El filtro de orígenes de Playwright **no es un aislamiento de red**: la documentación upstream
  advierte que no cubre redirecciones. No utilizar esta fase para navegar sitios no confiables
  con acceso a una LAN sensible sin aislamiento adicional de red/SO.
- El cliente de Home Assistant acepta únicamente una IPv4 privada explícita y `/api/mcp` o
  `/api/mcp/assist`. No hace descubrimiento de red, DNS, seguimiento de redirecciones, proxies de
  entorno, reintentos automáticos ni desactivación de TLS. HTTP sin cifrar requiere
  `allow_insecure_lan: true`; el bearer viaja en claro en esa LAN, por lo que se prefiere HTTPS.
  La primera fase solo consulta el contexto que el propietario exponga en Home Assistant;
  no controla luces, cámaras, cerraduras, alarmas ni otros dispositivos.
- Los procesos Node usan versiones fijas y lockfile, sin `npx` ni descarga al arrancar. Se
  comprueban huellas del ejecutable, entrypoint y lockfile. Las bibliotecas instaladas siguen
  formando parte de la base de confianza; estos controles no reemplazan un sandbox del SO ni
  protegen contra un atacante con capacidad de modificar todos los archivos del usuario.
- Filesystem puede ser ofrecido en ingeniería offline mediante una propiedad de su definición
  local, no por el nombre que anuncie el servidor. Los demás MCP siguen bloqueados por offline.
  Las menciones explícitas (`Filesystem`, `GitHub`, `Google Workspace`, `Playwright`,
  `Home Assistant`) solo activan herramientas disponibles y ya aprobadas, nunca conectan cuentas
  o habilitan configuraciones por sí mismas.

## Operación

Desde el checkout, con las dependencias Python existentes:

```bash
cd integrations/mcp
npm ci --ignore-scripts --no-audit --no-fund
cd ../..
./script/aegis.sh mcp-integrations
./script/aegis.sh mcp-prepare-local /ruta/absoluta/nueva-y-privada
./script/aegis.sh mcp-discover /ruta/absoluta/nueva-y-privada/filesystem.json

uv sync --project integrations/google-workspace --no-dev --locked
./script/aegis.sh mcp-prepare-google /ruta/absoluta/nueva-y-privada-google \
  --account TU_CORREO
./script/aegis.sh mcp-discover /ruta/absoluta/nueva-y-privada-google/google-workspace.json
```

`mcp-prepare-local` no sobreescribe directorios, no conecta servicios y no cambia la configuración
del daemon. Usa Documentos y Descargas del propietario. `mcp-discover` **ejecuta el peer elegido**
e inicializa su protocolo; solo enumera herramientas, no las llama ni aprueba. El comando exige
un archivo privado con un solo servidor. Nunca usarlo sobre un ejecutable no revisado.
`mcp-schema` continúa siendo el cálculo offline para servidores genéricos; los perfiles
curados usan la huella upstream separada de `mcp-discover`, no son intercambiables.
`mcp-prepare-google` también crea solo un perfil desactivado. Puede usarse antes de tener un
cliente OAuth para revisar el catálogo, sin consultar Gmail, Calendar o Drive.

Para la cuenta personal, crea un proyecto en Google Cloud y habilita Gmail API, Google Calendar
API y Google Drive API. Configura la pantalla de consentimiento, agrega la cuenta como usuario de
prueba si la audiencia es externa y crea un **cliente OAuth tipo Aplicación web** con la URI de
redirección exacta `http://127.0.0.1:18731/oauth2callback`. Regenera el perfil en una **carpeta
nueva** usando `--account TU_CORREO --client-id ID.apps.googleusercontent.com`; no edites los
pins ni pegues el client secret en JSON o en el chat. Introduce el secret mediante
`mcp-credential-set` en una terminal interactiva. El peer almacena los tokens en la carpeta
`credentials` de ese perfil (0700, archivos 0600). Tras añadir el perfil al daemon actualizado y
habilitarlo, la primera lectura autorizada inicia el consentimiento en el navegador del sistema;
repítela una vez concedido el acceso. Luego prueba creación y modificación con confirmación de un
uso. Un proyecto externo en estado «Testing» puede emitir refresh
tokens que caducan tras siete días. Después de publicar en Producción, completar de nuevo el
consentimiento para obtener un nuevo refresh token; no asumir que el token emitido en Testing
cambia su caducidad. En esta instalación, `https://jarvis.is-a.dev/jarvis`,
`https://jarvis.is-a.dev/jarvis/privacy` y `https://jarvis.is-a.dev/jarvis/terms` se comprobaron
por HTTPS y se guardaron en la información de marca, junto con el dominio autorizado
`jarvis.is-a.dev`. El callback continúa siendo local: el dominio aloja las páginas informativas.
Producción no equivale a verificación de Google; la app conserva el aviso de app sin verificar
y el límite de 100 usuarios para permisos sensibles/restringidos sin aprobar.

Para aprobar un peer: revisar su procedencia/versión, comando, valores predeterminados,
alcances, credenciales, huellas de artefactos y nombres/esquemas; rellenar `approved_tools` y
solo entonces habilitarlo. La configuración de ejecución es el array combinado `servers` de
`~/Library/Application Support/Aegis/mcp_servers.json`, con permisos 0600 y sin reemplazar
servidores existentes. Requiere un daemon que contenga estos cambios; no copiar perfiles nuevos
al daemon antiguo. Context7 permanece en su sistema de Capability Packs, sin modificaciones.

Las credenciales se introducen en una terminal interactiva, nunca en el chat ni en JSON:

```bash
./script/aegis.sh mcp-credential-set /ruta/github.json --connector GITHUB_PERSONAL_ACCESS_TOKEN
./script/aegis.sh mcp-credential-set /ruta/google-workspace.json --connector GOOGLE_OAUTH_CLIENT_SECRET
./script/aegis.sh mcp-credential-set /ruta/home-assistant.json --connector HOME_ASSISTANT_TOKEN
```

Solo se guarda el secreto solicitado en `ai.aegis.mcp.<server_id>` del llavero. No se reutilizan
credenciales de NVIDIA, IPC, plugins de Codex ni cuentas del navegador. Google además necesita su
flujo OAuth y almacenamiento seguro de tokens; guardar el client secret no conecta la cuenta.
Para Home Assistant primero debe existir el bloque `home_assistant` con dirección y cuenta de
llavero. No se ha elegido ni inventado una dirección del hogar del propietario.

## Evidencia

- Suite Python completa final: **1.866 passed, 17 skipped**; Ruff y `git diff --check` correctos.
  Las pruebas de sockets/navegador necesitaron ejecutarse fuera del sandbox de la herramienta;
  no se relajaron los controles de producción para hacerlas pasar.
- Node peers: `@modelcontextprotocol/server-filesystem@2026.8.31` y `@playwright/mcp@0.0.80`;
  instalación sin scripts y lockfile incluido. `npm audit --omit=dev`: cero avisos conocidos.
- `script/qualify_mcp_peers.py`: cinco herramientas Filesystem y tres Playwright admitidas;
  una llamada real autorizada por proveedor, salida verificada y replay de aprobación rechazado.
  La autorización sintética está limitada al proceso de pruebas: no otorga permisos al daemon.
- Google: instalación fijada `workspace-mcp==1.26.1`; arranque y descubrimiento real de las
  12 herramientas admitidas. Los digest hallados coinciden con
  `GOOGLE_WORKSPACE_REVIEWED_SCHEMAS`. Tres trabajos en la app instalada ejecutaron
  `search_gmail_messages`, `get_events` y `search_drive_files`, cada uno con aprobación de
  un solo uso y resultado verificado. Las consultas sintéticas devolvieron cero resultados.
  No se probaron escrituras. El 16 de septiembre se renovó el consentimiento en Producción
  y se validó el nuevo refresh token contra Google sin consultar contenido de las APIs.
  La suite de la implementación local posterior obtuvo **345 passed**, con Ruff y
  `git diff --check` correctos; no sustituye la evidencia de la suite completa anterior.
- Pruebas de políticas y transporte: `tests/test_mcp_integrations.py` y
  `tests/test_mcp_home_assistant.py`; GitHub/Home Assistant no se certifican end-to-end.
  La validación real de Google está limitada a las tres lecturas de prueba y OAuth.
- No hubo grabaciones, entrenamiento, pruebas con la voz del propietario ni órdenes domésticas.
- No se publica una release ni se declara estable una integración pendiente de credenciales.

## Fuentes revisadas

- [GitHub MCP oficial y configuración](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md).
- [Google Workspace MCP comunitario](https://github.com/taylorwilsdon/google_workspace_mcp/tree/v1.26.1): no es un servidor oficial de Google.
- [Google: consentimiento OAuth](https://developers.google.com/workspace/guides/configure-oauth-consent) y [caducidad de tokens en Testing](https://developers.google.com/identity/protocols/oauth2).
- [Filesystem de referencia](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem).
- [Playwright MCP oficial](https://github.com/microsoft/playwright-mcp).
- [Home Assistant MCP oficial](https://www.home-assistant.io/integrations/mcp_server/).
- [MCP Streamable HTTP 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
