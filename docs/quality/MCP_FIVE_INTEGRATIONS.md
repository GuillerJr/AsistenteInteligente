# Cinco integraciones MCP: implementación y estado real

Fecha: 2026-09-14. Fase experimental; no modifica el alcance congelado de Jarvis V1.
Estos conectores pertenecen a Jarvis, no a los plugins de Codex.

## Estado

| Integración | Implementado | Validación | Pendiente antes de uso personal |
| --- | --- | --- | --- |
| GitHub | Política positiva para consultar archivos, commits, issues y PRs; repositorios exactos; doble modo solo lectura | Pruebas de aislamiento de repositorio, método y credencial | Elegir repositorios, instalar/revisar el servidor oficial y autorizar credencial de lectura |
| Google Workspace | Políticas por herramienta para Gmail, Calendar y Drive; crear borradores, eventos, carpetas y texto; modificaciones acotadas | Pruebas de prohibición de borrado, papelera, permisos, envíos y rutas/URLs arbitrarias | Cuenta, OAuth, instalación/revisión de una versión fija del servidor comunitario y catálogo real |
| Filesystem | Servidor de referencia instalado; cinco herramientas de lectura; Documentos y Descargas preparados | Llamada real sobre archivo sintético, confirmación de un uso y descubrimiento del catálogo con las raíces elegidas | Activar el perfil revisado en un daemon actualizado |
| Playwright | Servidor oficial instalado; navegación, instantánea y cierre en navegador aislado | Arranque real y snapshot de navegador vacío, sin navegación de red ni sesiones personales | Elegir orígenes HTTPS y activar su perfil en un daemon actualizado |
| Home Assistant | Cliente MCP Streamable HTTP autenticado para LAN, JSON/SSE, sesiones y revisión de catálogo por llamada; solo `GetLiveContext` | Transporte simulado, control de credenciales, cambios de esquema, redirecciones y respuestas inválidas | Disponer de Home Assistant, comprobar compatibilidad de dispositivos y configurar dirección/token/exposición |

**No se conectaron cuentas ni se actualizó/reinició la app instalada en esta entrega.**
Los perfiles privados están en `.aegis/mcp-profiles/`, fuera de Git y con permisos 0600;
todos mantienen `enabled: false`. Filesystem y Playwright tienen sus huellas de esquema
revisadas; los otros tres no tienen herramientas aprobadas todavía. El perfil de Playwright
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
- Gmail solo permite leer y crear borradores de texto, sin adjuntos ni envío. Calendar admite
  crear/actualizar con `send_updates: "none"`; no borrar, cancelar, RSVP, invitados ni recurrencias.
  Drive permite metadatos seleccionados y añadir texto al principio/final, no reemplazar/vaciar,
  trasladar a la papelera, mover de carpeta, compartir o modificar permisos. No se ofrecen
  operaciones batch ni importación de archivos/URLs. Docs/Sheets/Slides completos no están incluidos.
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
```

`mcp-prepare-local` no sobreescribe directorios, no conecta servicios y no cambia la configuración
del daemon. Usa Documentos y Descargas del propietario. `mcp-discover` **ejecuta el peer elegido**
e inicializa su protocolo; solo enumera herramientas, no las llama ni aprueba. El comando exige
un archivo privado con un solo servidor. Nunca usarlo sobre un ejecutable no revisado.
`mcp-schema` continúa siendo el cálculo offline para servidores genéricos; los perfiles
curados usan la huella upstream separada de `mcp-discover`, no son intercambiables.

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
- Pruebas de políticas y transporte: `tests/test_mcp_integrations.py` y
  `tests/test_mcp_home_assistant.py`; GitHub/Google/Home Assistant no se certifican end-to-end.
- No hubo grabaciones, entrenamiento, pruebas con la voz del propietario ni órdenes domésticas.
- No se publica una release ni se declara estable una integración pendiente de credenciales.

## Fuentes revisadas

- [GitHub MCP oficial y configuración](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md).
- [Google Workspace MCP comunitario](https://github.com/taylorwilsdon/google_workspace_mcp): no es un servidor oficial de Google.
- [Filesystem de referencia](https://github.com/modelcontextprotocol/servers/tree/main/src/filesystem).
- [Playwright MCP oficial](https://github.com/microsoft/playwright-mcp).
- [Home Assistant MCP oficial](https://www.home-assistant.io/integrations/mcp_server/).
- [MCP Streamable HTTP 2025-11-25](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports).
