# OC-30: arnés Codex para cerrar A-21

El 6 de noviembre el dueño pidió empezar a cerrar familias de la fase A una a
una, usando REA. La primera elegida es **A-21, Codex con ChatGPT oficial y
aislado**, porque las demás dependen de ella. Su aceptación exige: login oficial
en espacio dedicado, catálogo, chat, herramienta y continuidad tras reinicio;
y diagnósticos acotados para cuenta vencida, CLI ausente, desconexión y thread
ajeno, sin leer `auth.json` ni importar chats personales.

De las 535 fuentes no de prueba de `extensions/codex`, la revisión previa sólo
cubría 14 (OC-05, OC-29, OC-RESOURCES-A). OC-30 añade las que gobiernan esos
criterios de aceptación.

## Identidad y evidencia

Referencia de sólo lectura: OpenClaw `1e3ff306abaff29f03e7668d53b01c07f10294ac`,
árbol `197c70097790e30087bbef323b0f2cc5148ab9b7`. Se extrajeron 22 blobs exactos
con `git cat-file` a la partición inerte
`/private/tmp/jarvis-openclaw-a21-rea-20261106` (raíz SHA-256
`6ad100fc9cb723ed630d1b0fde7df28cecb48f7f8dfcbf58efd927b923427992`). No se
ejecutó, instaló, compiló ni modificó OpenClaw, y sus fuentes no se incorporan
al producto.

REA analizó las 22 rutas (21 TS y `package.json`): 114.211 bytes, 12.043 nodos
AST, 374 hallazgos, sin fallos de parseo. Padre
`ev_a0fafeb5ba6edcf4d0d3fb41361393f45391f9d76340139d1601269e1dc278b2`; resumen
`ev_6f05467244afd19daa8a97294c532838768c610dade2150a8382cb8cc0518c56`. La traza
de la semilla `account/` (`ev_7ef984a5f066168ee606ed693eb013d0494603eb063328d13be996245d5c6ac2`)
alcanzó 275 nodos y 342 aristas: recorre casi todo el grafo y no aísla un
camino, así que el comportamiento se cita de la lectura directa de los mismos
blobs. Quedan 2051 incógnitas semánticas; es análisis estático, no ejecución.
La guía `docs/plugins/codex-harness-reference/auth.md` (9370 B, SHA-256
`b79fcafbaada48a4e6233e3dba1fbfc53f58717e8a78fc49ba8f921587ebf3bc`) se leyó
aparte.

| Fuente (`extensions/codex/…`) | SHA-256 |
| --- | --- |
| `client-version-api.ts` | 95b1be2ed6cb90789638f303064e91f7bc192dfb8962f6a8bbec5976ab27d3a7 |
| `package.json` | 474e0861c60715750fc241775daf85833aab6e47c80ed23aab39a6cc3b375193 |
| `src/app-server/attempt-terminal.ts` | 94a3ff4445c9d44690e84fd87aeb52547959865a35020d42d54dd391a5422509 |
| `src/app-server/attempt-timeouts.ts` | 5274b1712b2be6a62a1a89f7c48dbc70c6a7fc8c32d36380479b2ca620561217 |
| `src/app-server/auth-binding.ts` | 79489b7e1a8249227ee7ec2faca733698b35b3bbf06410aac6ba82fae071b456 |
| `src/app-server/auth-bridge.ts` | e8922d50001fb5afcca4b0bfc41e90aa1a7ae848706cf0cf40c5d207985f5c0f |
| `src/app-server/auth-profile-recovery.ts` | ea11f5f0129eafd96a371e6ff1adab011f3c0bdd631336154c6165cfaf389b9b |
| `src/app-server/auth-profile-selection.ts` | e048d9f0c54a7c8b9598961d82d85cbf75f271043745d554cfc3117ccd45b5b9 |
| `src/app-server/auth-profile.ts` | c5acba78c54346640f8df87cbabe09783a23fe0da0f8ec154263d7884585f31d |
| `src/app-server/auth-start-options.ts` | 7897dea66005ad76690eeb2d487f9fcf5bc5549dab034e53b32be89e1c3a7c15 |
| `src/app-server/auth-types.ts` | 590000c9745c3761e5254830431e4ff009ad76fc8c401f78c73f4d2634c63b6e |
| `src/app-server/client-initialize.ts` | 317a82bb89439186ebf5818c5e0a7ca40788c648de4128d86f06bed659fe31a1 |
| `src/app-server/client-startup-retry.ts` | 3e89a55217c287b9e4b7dfd396d73bad385897363665a983274cc466b98022fc |
| `src/app-server/client-thread-closure.ts` | 6248adf5fe8cb6490d804abbf4b404ed5f475b1d00feec436225a2ad812f0b38 |
| `src/app-server/client-thread-owner.ts` | 581c86f579492eb090bdd122fda7a2c95796e3ca01ebe2d1d2e5582f8b4f0290 |
| `src/app-server/model-catalog.ts` | 1e0734894c21c2f3c2130071e20f74496907eb72359f77e6d871190b5b206fa6 |
| `src/app-server/models.ts` | 759ad332abacfd4224fcaafe22a21129fb9e4b15abc810fd78152fef994a1bab |
| `src/app-server/native-auth.ts` | 16a23d1cc4fb51955d3c04892d1215e0366de96e8de963d5d2e810e80b08bf01 |
| `src/app-server/thread-binding-policy.ts` | 3e12e7bd207439c16541fcfd6f2e4b091cf3658a1ee70eabf4ae0b4bf37ad35e |
| `src/app-server/thread-model-selection.ts` | 0fa7bedc7b382cac8e1e35e16a5171ecb0e656c5d45c81e7091c3f229853bdc2 |
| `src/app-server/thread-restricted-resume.ts` | f238aeb8080c9bbcb1cb869cb458a288d0ea1e611d9cd7753d369b8faab7c979 |
| `src/app-server/thread-resume.ts` | 27a7de804df2f7e0a5f7f80a12c85dc362cec567dcd0f68ae236efa4181e4217 |

## Comportamiento observado en las fuentes

1. **Versión de Codex comprobada en el arranque.** `client-initialize.ts`
   lee la versión del `userAgent` de la respuesta de `initialize`
   (`<originador>/<versión> …`), rechaza con un error propio si falta o es
   inferior a la mínima admitida, y sólo avisa si es más nueva que la validada.
   También envía `optOutNotificationMethods` y declara extensiones de
   elicitación y formularios.
2. **Catálogo de modelos real.** `models.ts` pagina `model/list`
   (`limit`, `cursor`, `includeHidden`) y valida cada modelo (id y nombre no
   vacíos). `model-catalog.ts` lee primero `account/read` para drenar el
   `account/updated` diferido del login, lista los modelos, oculta los
   `hidden` salvo los configurados, y publica el resultado sólo si la
   operación acotada sigue siendo la vigente; si vence el plazo, retira el
   cliente compartido en lugar de abortar a otros.
3. **Cuenta ligada a la ruta.** `auth-bridge.ts` verifica con `account/read`
   que la cuenta del hogar nativo coincide con la ruta: suscripción exige
   `chatgpt`, y una ruta de API rechaza una cuenta ChatGPT para no gastar el
   plan del operador. La guía de auth fija que, en el hogar por agente, no se
   lee un `auth.json` existente salvo importación explícita.
4. **Reanudación segura.** `thread-resume.ts` valida la respuesta de
   `thread/resume` y que el thread suscrito sea exactamente el pedido; ante un
   error RPC posterior a la suscripción intenta `unsubscribe` sólo de ese
   thread y, si no puede, retira el cliente y lo marca como suscripción
   insegura.
5. **Arranque acotado.** `attempt-timeouts.ts` distingue arranque `aborted` y
   `timed_out` con un código propio, añade un diagnóstico de stderr al error de
   `initialize` y acota la liquidación del turno terminal (2 min) y el drenado
   tras abortar (5 s).

## Contraste con Jarvis en `67fbd6b`

| Criterio A-21 | Jarvis hoy | Brecha |
| --- | --- | --- |
| Login oficial en espacio dedicado | `account/login/start` tipo `chatgpt`, URL validada contra `auth.openai.com`, `CODEX_HOME` propio, sin claves heredadas | Sin brecha de diseño; falta recorrido nativo del dueño en la cualificación de cierre. |
| Cuenta y modo | `account/read` sin refresco; sólo `chatgpt` cuenta como conectado | Equivalente a la ruta de suscripción. No observa `account/updated` ni el login completado. |
| **Catálogo** | **No llama a `model/list`; no hay catálogo ni elección de modelo** | **G1 (bloquea A-21).** |
| **Versión de Codex** | **`initialize` no lee `userAgent` ni exige versión** | **G2.** Hoy dependemos de comportamientos de 0.163 (`jarvis_direct`, `phase`). |
| Chat y herramienta | Cubierto; caso nativo V | Sin brecha para un PNG; el resto pertenece a A-07/A-56. |
| **Reinicio / reanudación** | **`thread/resume` ignora la respuesta y no comprueba el thread suscrito** | **G3.** |
| Desconexión, CLI ausente | `codex_disconnected`, errores con código propio | Falta ensayo explícito de CLI ausente y de cuenta vencida en cualificación. |
| Thread ajeno | Sólo se reanudan bindings propios; no hay `thread/list` | Sin brecha. |
| Diagnóstico de stderr | Se descarta siempre | Diferencia deliberada: no se persisten prompts ni credenciales. Se mantiene. |
| Credenciales | `cli_auth_credentials_store="file"` en el hogar propio | Diferencia: OpenClaw usa almacén efímero en el hogar por agente; Jarvis necesita persistir su propio login para el reinicio. Se mantiene. |

## Plan de cierre de A-21

1. **G2, versión mínima.** Leer la versión del `userAgent` de `initialize`,
   exigir `>= 0.163.0` con un código propio (`codex_version_unsupported`) y
   registrar la versión observada. Requiere actualizar todos los peers
   sintéticos que hoy responden `{}` a `initialize`.
2. **G3, reanudación.** Validar `thread/resume` y que `thread.id` sea el pedido;
   si no, marcar el thread como incierto y no reutilizarlo.
3. **G1, catálogo.** Paginar `model/list` tras `account/read`, validar ids,
   ocultar `hidden`, exponer el catálogo por IPC y en la app, y conservar el
   modelo elegido por sesión. La elección de proveedores adicionales sigue en
   A-22 y no se mezcla aquí.
4. **Cualificación nativa de cierre**, con recursos aislados y el dueño
   haciendo el login: login, catálogo, chat con herramienta, reinicio y
   continuación; y negativos de cuenta cerrada, CLI ausente, desconexión y
   versión no admitida.

Estado de implementación:

- **G2 y G3 implementados** (06-11-2026). `CodexClient.start()` lee la versión
  del `userAgent` de `initialize` (originador sin `/`, versión `x.y.z`, como en
  OpenClaw), la registra en `codex_version` y rechaza con
  `codex_version_unsupported` una versión ausente, ilegible, inferior a 0.163.0
  o pre-release de 0.163.0, antes de cualquier otra petición; la app lo muestra
  en español. `thread/resume` exige `thread.id` igual al binding pedido; si no,
  el thread queda incierto y responde `codex_thread_recovery_required` sin
  reanudarse otra vez. Diferencia con OpenClaw: Jarvis no intenta
  `unsubscribe`; marca el binding y no lo reutiliza. Peers sintéticos de Codex
  actualizados; regresiones en `tests/test_codex.py`.
- **G1, catálogo, implementado** (06-11-2026). `CodexClient.list_models()`
  lee `account/read` y luego pagina `model/list` (100 por página, sin ocultos,
  como máximo 8 páginas), valida id, nombre visible, `isDefault` y modalidades,
  rechaza duplicados y cursores inválidos, y omite los modelos `hidden`. El
  servicio lo expone como `provider.models` de lectura, sólo para el dueño
  (como `service.status`, porque consulta al SDK) y bajo la elección de
  proveedor vigente. La app lo muestra a demanda en Ajustes › Cuenta de ChatGPT
  con el predeterminado marcado. Listar no elige modelo: Jarvis sigue usando el
  predeterminado de Codex y la elección pertenece a A-22. La compilación Swift
  se valida en CI.

Diseño de la cualificación nativa (punto 4): [PHASE_A_A21_QUALIFICATION_PLAN.md](PHASE_A_A21_QUALIFICATION_PLAN.md).

Sólo con los cuatro puntos y su CI completa, más el recorrido nativo, A-21
pasaría a completa. Hasta entonces sigue en `implementacion_parcial_contratos_probados`.

## Límites

Lectura estática parcial: no se revisaron `run-attempt-connection.ts`,
`startup-binding.ts`, `session-binding*.ts`, `rate-limits.ts` ni
`account-usage-runtime.ts` (los dos últimos pertenecen a A-23). Las 2051
incógnitas semánticas siguen abiertas y OpenClaw no se ejecutó.
