# OC-31: uso, límites y cuenta de Codex para A-23

Segunda familia de la fase A que se cierra una a una con REA, por elección del dueño
del 6 de noviembre, tras cerrar A-21. **A-23 (autenticación, renovación, cuentas, uso
y límites)** exige: login y logout oficiales con el dueño; uso y límites atribuidos al
proveedor y a la sesión efectivos; una cuenta revocada deja de usarse; los registros
no muestran tokens; y un dato de límite ausente no aparece como cero. El alcance de
esta revisión es el proveedor elegido, Codex con ChatGPT; otros proveedores requieren
elección explícita (A-22).

## Identidad y evidencia

Referencia de sólo lectura: OpenClaw `1e3ff306abaff29f03e7668d53b01c07f10294ac`,
árbol `197c70097790e30087bbef323b0f2cc5148ab9b7`. Se extrajeron 16 blobs exactos con
`git cat-file` a la partición inerte `/private/tmp/jarvis-openclaw-a23-rea-20261106`
(SHA-256 de REA `1fc0af67b5cc7a57fffb504462917bb7539b00e08f734330d05176978eb584ed`).
No se ejecutó, instaló, compiló ni modificó OpenClaw, y sus fuentes no se incorporan
al producto.

REA analizó 16 rutas (15 TS y un JSON de protocolo): 129.043 bytes, 15.167 nodos AST,
323 hallazgos, sin fallos de parseo. Padre
`ev_d6cf3649a8a66ecaf1594273aee9d9f72dc7788498cdda6b3543a9e8580ae008`; resumen
`ev_1150914f61f623631a88dc1c41cc92d27fee5d6bde0a978f24745a62d15654a8`. La traza de la
semilla `rateLimits` (`ev_da02aedb66d03c49a806665c6e205a43d102638af9e54841b4ded5d937c1d63d`)
alcanzó 12 coincidencias, 51 nodos y 63 aristas; la de `account/rateLimits`
(`ev_4df01647…`) no encontró nodos, lo que no prueba ausencia (el nombre del método
vive en `capabilities.ts`, fuera de la partición). Quedan 2788 incógnitas semánticas;
es análisis estático, no ejecución. Lectura directa aparte, con `git cat-file`: el caso
`error` de `event-projector.ts`, el nombre del método en `capabilities.ts` y los tipos
`TurnError`/`CodexErrorInfo` de `CodexAppServerProtocolDefinitions.json`. Hashes de cada
blob en [REA_SUMMARY.json](qa/openclaw-codex-a23-20261106/REA_SUMMARY.json).

## Comportamiento observado en las fuentes

1. **Lectura de límites.** `request.ts` pide `account/rateLimits/read` en un cliente
   aislado y de sólo lectura, con plazo propio, y lee el correo de la cuenta como dato
   opcional. `account-usage-runtime.ts` sólo lo admite para un login de suscripción
   ChatGPT (nunca clave de API ni proxy), fuerza un hogar por agente y comprueba antes
   y después de la lectura que la credencial no cambió.
2. **Forma del dato.** `rate-limits.ts` valida instantáneas con ventanas `primary` y
   `secondary` (`usedPercent`, `resetsAt` en segundos, `windowDurationMins`), `credits`
   (`balance`, `unlimited`), `planType` y `rateLimitReachedType`, también agrupadas por
   `rateLimitsByLimitId`. Sólo muestra datos presentes: una ventana ausente no se
   rellena, y el saldo sólo se publica si es ≥ 0 y no ilimitado.
3. **Actualizaciones.** `client-runtime.ts` atiende `account/rateLimits/updated` y
   `rate-limit-cache.ts` funde esa actualización parcial sobre la última lectura del
   mismo cliente físico (caché de 10 min, con revisión que sólo avanza con datos
   del límite principal `codex`).
4. **Error de límite de uso.** En la notificación `error` de un turno,
   `error.codexErrorInfo` distingue `usageLimitExceeded` (cuota de la suscripción
   agotada: mensaje con la próxima renovación y el perfil bloqueado hasta entonces) de
   `rateLimitExceeded` (429, sin heredar el bloqueo), `serverOverloaded` (503),
   `internalServerError` y fallos HTTP con su código. `willRetry: true` no es terminal.
   El protocolo también define `unauthorized`, `contextWindowExceeded` y
   `sessionBudgetExceeded`.
5. **Renovación.** OpenClaw siempre llama `account/read` con `refreshToken: false`; la
   renovación de credenciales queda dentro del cliente oficial. No se observó un
   refresco propio de tokens en estas fuentes.

## Contraste con Jarvis en `5c9cf92`

| Criterio A-23 | Jarvis hoy | Brecha |
| --- | --- | --- |
| Login/logout oficiales | `account/login/start` y `account/logout`, acreditados en nativo por W1 | — |
| Tokens fuera de registros | stderr del cliente se descarta; nunca se lee `auth.json` | — |
| Límites y plan | No llama a `account/rateLimits/read` | **G1** |
| Actualizaciones de límites | Ignora `account/rateLimits/updated` | **G2** |
| Error de límite / revocación en turno | Ignora la notificación `error`; todo acaba en `codex_turn_failed` | **G3** |
| Revocación externa durante la sesión | Sólo se ve en el siguiente `account/read` | **G4** (falta revisar `client.ts`) |
| Uso atribuido | Tokens medidos por run alimentan presupuestos, sin exposición por run, proveedor y sesión | **G5** |

## Plan de cierre de A-23

- **G1.** RPC de sólo lectura del dueño `provider.limits`: `account/rateLimits/read`
  validado y acotado, normalizado a plan, ventanas, créditos y límite alcanzado;
  cualquier dato ausente sale `null`, nunca `0`. Ajustes en español.
- **G2.** Fundir `account/rateLimits/updated` en una caché por cliente, descartada al
  cambiar de cliente, cuenta o elección de proveedor.
- **G3.** Leer `error` en el bucle del turno: `usageLimitExceeded` →
  `codex_usage_limit_reached` (con la renovación si se conoce), `rateLimitExceeded` →
  `codex_rate_limited`, `serverOverloaded` → `codex_server_overloaded`,
  `unauthorized` → `codex_login_required`; `willRetry` sólo informa. Mensajes en
  español y sin repetición automática.
- **G4.** Revisar con REA el manejo de `account/updated` en `client.ts` y decidir la
  revocación inmediata.
- **G5.** Exponer los tokens medidos por run con proveedor y sesión; sin medición,
  `null`.
- Después, cualificación nativa con la cuenta del dueño: lectura real de límites y,
  si el dueño lo aprueba, un negativo de revocación.

## Estado de implementación (06-11-2026)

- **G1 implementado.** `CodexClient.read_limits` lee `account/rateLimits/read` sólo con
  cuenta ChatGPT, valida y acota cada campo (texto, porcentaje, renovación, ventana,
  créditos) y deja `null` todo dato ausente o inválido. RPC de sólo lectura del dueño
  `provider.limits`, con las mismas guardas de elección de proveedor que
  `provider.models`. La app muestra «Ver límites de uso» en Ajustes, y una cuenta sin
  límites informa que un dato ausente no significa uso cero.
- **G2 implementado.** `account/rateLimits/updated` se funde en la caché del proceso
  actual: ventanas y estado alcanzado del update mandan; plan, créditos y nombre nulos
  conservan la última lectura. Las actualizaciones que llegan tras la respuesta de una
  lectura se aplican encima (gancho `on_reply` del lector, en orden de cable); las
  anteriores quedan superadas. La caché se vacía al arrancar otro proceso y al cerrar
  sesión.
- **G3 implementado.** La notificación `error` del turno clasifica `usageLimitExceeded`,
  `rateLimitExceeded`, `serverOverloaded` y `unauthorized` en códigos propios
  (`codex_usage_limit_reached`, `codex_rate_limited`, `codex_server_overloaded`,
  `codex_login_required`), también desde `turn.error`; `willRetry` no es terminal y el
  mensaje del proveedor nunca se copia. Mensajes en español sin repetición automática.
- **G5 implementado.** Cada run guarda `usage` con proveedor, sesión y tokens medidos por
  el proveedor: `total_tokens` sólo con cuenta final, `partial_tokens` si no la hubo, y
  `null` sin medición.
- **G4 pendiente** de revisar `client.ts` con REA.

Pruebas focales supervisadas: `tests/test_codex.py`, `tests/test_service.py`,
`tests/test_codex_usage.py` y las de perfil, puente, revisión, herramientas y custodia
pasan; `test_service_capabilities.py` no puede crear su temporal bajo el guard local
(limitación conocida, cubierta por CI). Las pruebas Swift (`ProviderLimitsTests`,
`RPCFrameTests`) no compilan su manifiesto dentro del sandbox local y quedan para CI. La
cualificación completa depende de CI y del recorrido nativo con la cuenta del dueño.

## Límites

Lectura estática parcial: no entran `client.ts`, `run-attempt-connection.ts`, el
resto del proyector de eventos ni la renovación interna del cliente oficial. OpenClaw
no se ejecutó y las 2788 incógnitas semánticas siguen abiertas. A-23 sigue en
`implementacion_parcial_contratos_probados`.
