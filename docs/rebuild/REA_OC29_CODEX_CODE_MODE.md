# OC-29: modo de código del arnés Codex de OpenClaw

El 5 de noviembre el dueño pidió ampliar la revisión REA antes de corregir el
fallo de los casos visuales Q y R. El rollout de R mostró que el modelo llamó a
`media_inspect` desde la herramienta `exec` del modo de código de Codex, que
no pudo arrancar `codex-code-mode-host`. La revisión previa del arnés Codex de
OpenClaw sólo cubría cuatro archivos (salud de conexión, propiedad del thread y
dos de respuestas dinámicas); el modo de código y la carga de herramientas no
se habían leído.

## Identidad y evidencia

Referencia de sólo lectura: OpenClaw `1e3ff306abaff29f03e7668d53b01c07f10294ac`,
árbol `197c70097790e30087bbef323b0f2cc5148ab9b7`. Se extrajeron once blobs
exactos con `git cat-file` a la partición inerte
`/private/tmp/jarvis-openclaw-codemode-rea-20261105` (raíz SHA-256
`bef39b862f37d9448a8f5769671ae24b0586eedb73f1f4bda33a2a414bd2082b`). No se
ejecutó, instaló, compiló ni modificó OpenClaw, y sus fuentes no se incorporan
al producto.

REA analizó diez rutas seleccionadas (nueve TS y `package.json`): 117.256 bytes,
11.560 nodos AST, 300 hallazgos, sin fallos de parseo. La guía
`dynamic-tools.md` se leyó por separado. Padre
`ev_e91b28823c4099e6398aa6fd7d3c9e790d4d95bb86bdbbe0254ed564a64c7b67`; resumen
`ev_afe3e3939b52ed15c01c19b088557f7452c823702c6a722af88b6f8ba0f7fded`. La traza
semántica de la propiedad `direct_only_tool_namespaces` la sitúa de forma exacta
en `thread-requests.ts:346`
(`ev_375bf1316dcea8162871c3cdd5596001e72a095de28fb01e7407574d1e50ab99`). Los
literales compuestos `features.code_mode` no son nodos del grafo
(`ev_ca5147d8…`, sin coincidencias); esas líneas se citan de la lectura directa
de los mismos blobs. Quedan 2018 incógnitas semánticas; la cobertura semántica
es parcial y esto es análisis estático, no ejecución.

| Fuente | Bytes | SHA-256 |
| --- | ---: | --- |
| `extensions/codex/src/app-server/thread-requests.ts` | 29100 | 56c2399d53f4c58ddc6426de39e4f5a7a1ec6ee837f68e9562ea22ffb9b8d2ad |
| `extensions/codex/src/app-server/dynamic-tool-catalog.ts` | 10115 | 22a387612d6cc9b41440a5a229ff74b03bc1f4bee6f4963b7f609f513247863f |
| `extensions/codex/src/app-server/dynamic-tool-profile.ts` | 4568 | 6af47ae3f8fd64a43b06a4cf225e60c6cba4c8094168f9547751aea97b5833cf |
| `extensions/codex/src/app-server/native-tool-catalog.ts` | 5012 | b7d3bcfd49819f1c3aa8debe589504c0fba613fc2dd5e6d7b535bd59e68f1077 |
| `extensions/codex/src/app-server/event-projector-code-mode.ts` | 4622 | aa1afe0491d9cd0834e6e0ceddbb0b3b07cc7ffe46ebf34eb01a18d5ecf62271 |
| `extensions/codex/src/app-server/bounded-turn.ts` | 21869 | e5e540ff31a7e5042f9c96b1870969dc7af3628f11ea73053c9f07f23c3b330b |
| `extensions/codex/src/app-server/client-notifications.ts` | 3964 | 6b0efa950c823fc26354df7bf6b0b0a3fd31bf49984c59878c848bf82befb368 |
| `extensions/codex/src/app-server/attempt-startup.ts` | 30179 | 53626abe18f8fdebd157010e68d7ca05f4e905ef45fe5738bc055be7283c6f54 |
| `extensions/codex/media-understanding-provider.ts` | 6338 | d2d08777517e6b9e1cc1cde9582046b523d5310b9df6a3b45c3e107b4b205374 |
| `extensions/codex/package.json` | 1489 | 474e0861c60715750fc241775daf85833aab6e47c80ed23aab39a6cc3b375193 |
| `docs/plugins/codex-harness-reference/dynamic-tools.md` (lectura aparte) | 4911 | c2497cab489f32bd241ea7c5dd71f508aad74be5d95cd6114861c8de2b318756 |

## Comportamiento observado en las fuentes

1. **OpenClaw decide el modo de código en cada thread; no hereda el valor por
   defecto de Codex.** `buildCodexRuntimeThreadConfig` (`thread-requests.ts:266-319`)
   tiene dos ramas. Con la superficie nativa activa aplica
   `features.code_mode: true`, `code_mode_only` según configuración y
   `shell_tool: true` (50-59, 283-285). Desactivada, fija
   `features.code_mode`, `features.code_mode_only`, `features.shell_tool` y
   `features.unified_exec` a `false` (295-301). En ambos casos fija
   `features.tool_suggest`, `features.recommended_plugins` y `features.goals` a
   `false` (287-294). La superficie nativa sigue a `nativeToolSurfaceEnabled`
   (`attempt-startup.ts:467`).
2. **Herramientas sólo directas.** Las herramientas dinámicas con
   `catalogMode: "direct-only"` se agrupan en un namespace propio
   (`dynamic-tool-catalog.ts:89-104`) y ese namespace se añade a
   `features.code_mode.direct_only_tool_namespaces` sin reemplazar las entradas
   del operador (`thread-requests.ts:337-371`). Según la guía, Codex las expone
   como `DirectModelOnly` y no como `tools.*` dentro de `exec`, porque el modo
   de código aplana a texto los resultados con imagen y porque una aprobación
   humana debe quedar en la llamada directa: un `exec` cedido no puede dejar que
   el modelo termine el turno con la aprobación pendiente.
3. **Los turnos acotados y la comprensión de imágenes no usan modo de código.**
   `bounded-turn.ts:266-285` construye el thread con `nativeCodeModeEnabled:
   false`, `dynamicTools: []`, `sandbox: "read-only"` y
   `approvalPolicy: "on-request"`. `media-understanding-provider.ts` delega
   `describeImage(s)` en ese turno acotado.
4. **Superficie restringida.** El perfil «ring zero» desactiva por lista, entre
   otras, `code_mode`, `code_mode_only`, `browser_use`, `computer_use`,
   `goals`, `hooks`, `multi_agent`, `multi_agent_v2`, `unified_exec` y
   `view_image`, y también `agents.enabled` (`thread-requests.ts:65-110`).
5. **Observación del modo de código cuando está activo.**
   `event-projector-code-mode.ts` reconoce envoltorios `exec` que llaman a
   `tools.apply_patch`/`tools.exec_command` sin evaluar JavaScript, para
   proyectar esas llamadas nativas.
6. `client-notifications.ts:15` reconoce el aviso de Codex cuando el modo de
   código está activo para un modelo que no lo anuncia.

## Contraste con Jarvis en `613f210`

- `CodexClient.start()` pasa sus opciones con `-c` al proceso y **no fija
  `features.code_mode`**: Codex aplica su valor por defecto. El rollout de R
  confirma que el modelo recibió `exec` y llamó desde ahí a `media_inspect`.
- Jarvis no marca ninguna herramienta como sólo directa. `media_inspect`
  devuelve imagen y requiere aprobación: es el caso exacto que OpenClaw saca
  del modo de código. Además, Jarvis rechaza llamadas con namespace no nulo,
  así que adoptar `direct_only_tool_namespaces` exigiría cambiar ese contrato.
- Jarvis no fija `features.goals`, `features.multi_agent_v2` ni
  `agents.enabled`; el rollout de R registró `multi_agent_version: "v2"` aunque
  `features.multi_agent=false`.
- OpenClaw describe imágenes en un turno acotado sin herramientas; Jarvis
  entrega la vista aprobada por `turn/steer` dentro del mismo turno. Son
  diseños distintos y esta lectura no acredita que el de Jarvis sea equivalente.

## Conclusión y límites

El fallo de R no está en la idea de usar REA ni en Codex, sino en una parte del
arnés que no se había revisado: OpenClaw nunca deja el modo de código a su valor
por defecto y aparta del modo de código las herramientas con imagen o con
aprobación. La corrección coherente con la referencia para Jarvis, que no expone
superficie nativa, es la rama desactivada: `features.code_mode=false`,
`features.code_mode_only=false` (con `shell_tool`/`unified_exec` ya en
`false`), más `features.goals=false`, `features.multi_agent_v2=false` y
`agents.enabled=false`. Se implementó después de esta revisión, por decisión del
dueño, en `CodexClient.start()` con regresión en el peer sintético; falta el
caso nativo S.

Incógnitas: no se ha verificado que `features.code_mode=false` retire `exec`
en Codex 0.163 con `code_mode_host` activo; OpenClaw depende de ello pero esta
lectura no lo ejecutó. Las fuentes internas de Codex no se inspeccionaron. Las
2018 incógnitas semánticas, `dynamic-tool-profile.ts` y `native-tool-catalog.ts`
quedan como lectura parcial. Esta partición no amplía la cobertura a todo el
arnés Codex ni a OpenClaw.
