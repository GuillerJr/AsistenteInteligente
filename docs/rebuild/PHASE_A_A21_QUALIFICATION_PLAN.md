# Cualificación nativa de cierre de A-21: diseño

Estado: **diseño, sin ejecutar**. Ningún caso de este documento se ha preparado,
lanzado ni congelado. Cada subcaso se congelará NOEXEC y esperará el visto bueno
explícito del dueño, como Q–V.

## Qué hay que acreditar

Criterio de la matriz para A-21: el dueño completa el login oficial en un espacio
dedicado; Jarvis obtiene el catálogo, chatea, usa una herramienta y continúa tras
un reinicio. Cuenta vencida, CLI ausente, desconexión y thread ajeno se
diagnostican sin leer `auth.json` ni importar chats personales.

Decisiones del dueño (06-11-2026):

- **Proveedor nuevo.** Cuota APFS y `CODEX_HOME` nuevos y vacíos; el dueño inicia
  sesión en el navegador durante el caso. El proveedor de
  `/private/tmp/jq-8k57q6f0` (ya con sesión) no se usa para cerrar A-21.
- **Cierre de sesión como negativo final.** El proveedor de prueba queda sin
  sesión; no afecta a la instalación ni a otros perfiles del dueño.

## Por qué no basta el arnés de Q–V

El arnés de Q–V reutiliza una cuota y un proveedor existentes y admite un único
lanzamiento por caso. A-21 necesita además: crear cuota y proveedor vacíos (como
el caso E del 04-11, cuyo `prepare.py` lo hacía con el formato de admisión de
entonces), un login del dueño dentro del caso, dos lanzamientos encadenados de la
misma app para el reinicio y negativos con otro binario.

## Subcasos

| Caso | Recorrido | Negativos | Base del arnés |
| --- | --- | --- | --- |
| **W1** | Cuota y proveedor nuevos; el dueño pulsa «Abrir inicio de sesión» y completa el login en el navegador; `service.status` conectado; `provider.models` no vacío y con predeterminado; chat con una herramienta ligada (el PNG de V) | Cierre de sesión al final y `runs.start` rechazado con `codex_login_required` | Preparación de E (cuota nueva) + comprobaciones de CI y ZIP predescargado de V + escenario de V ampliado |
| **W2** | Mismo proveedor de W1 tras volver a iniciar sesión el dueño; lanzamiento 1: chat en un chat persistente; cierre GUI; lanzamiento 2 de la misma app: continuar ese chat (`thread/resume` del binding propio) con una pregunta que sólo se responde con el turno anterior | Sin `thread/list`; ningún thread ajeno reanudado | Encadenamiento de custodia de Q–V con dos lanzamientos |
| **W3** | — | CLI ausente (ruta de Codex inexistente): error acotado sin lanzar inferencia; versión no admitida: binario sintético propio que anuncia `0.162.0` en `initialize` y responde `codex_version_unsupported`; desconexión: fin del proceso Codex propio durante un turno y `codex_disconnected` sin repetición | Lanzamientos sin inferencia real; el binario sintético nunca es el cliente oficial y no contacta a OpenAI |

Orden: W1, W2, W3. W2 depende del proveedor de W1; W3 es independiente de la
cuenta salvo el caso de desconexión, que usa el proveedor de W1.

## Límites heredados

Cuota 2 GiB; proveedor nuevo con tope de 128 MiB muestreado; Data libre ≥25 GiB y
residual ≤1 GiB; preparación 300 s con el ZIP descargado antes de la ventana;
ventana activa 810 s + 90 s de cierre por lanzamiento. Un efecto incierto se
conserva y no se repite. El dueño escribe la contraseña; ni Jarvis ni el agente
leen `auth.json`, tokens o historial personal.

## Qué cierra A-21

W1, W2 y W3 pasados, sobre un commit con CI completa en verde, más G1–G3 ya
implementados (OC-30). Sólo entonces A-21 pasa a completa en la matriz.

## W1 ejecutado (06-11-2026): pasado

Arnés `/private/tmp/jarvis-a21-native-20261106-w1`: preparación de cuota nueva tomada del
caso E y comprobaciones de CI/ZIP predescargado de V; escenario de V ampliado. Binding
`aecdaa12d9c150b51a5d894f362772adad257a62`, CI completa 42239927920 en verde (macOS
127967201013, Ubuntu 127967200411), artefacto 13514757242 (7.947.984 B, SHA-256
`a6cf082c…`) predescargado. Admisión `66d1eee9…`, preparado `bad2f858…` con cuota nueva
`/private/tmp/jq-tqbfd_1m` y `provider/` vacío; cierre `closed_after_GUI_intent`
(`8ec5da48…`) sin señales y cuota desmontada.

Recorrido (recibo `w1-result.json` `57c4911b…`, `passed_A21_W1_case`):

1. **Login del dueño.** Root pulsó «Conectar ChatGPT»; el dueño completó el login oficial
   en el navegador. `service.status` pasó a conectado, modo `chatgpt`. Nadie leyó
   `auth.json`.
2. **Catálogo.** `provider.models` devolvió 8 modelos visibles con `gpt-6.1-sol` como
   predeterminado; listar no eligió modelo.
3. **Herramienta.** Una llamada directa a `media_inspect` con una aprobación ligada,
   recibo `known`/`succeeded`, entrega CAS de la vista aprobada y respuesta igual al
   oráculo oculto de la imagen nueva (triángulo azul a la izquierda, círculo rojo en el
   centro, cuadrado verde a la derecha).
4. **Cierre de sesión.** `provider.logout` confirmado; `service.status` desconectado;
   `provider.models` vacío y desconectado; `runs.start` rechazado con
   `-32037 provider_choice_required` y un único run en la conversación.

Límites: un hogar Codex, un login, este catálogo, un PNG y un cierre de sesión. El
proveedor de W1 queda sin sesión; W2 necesita que el dueño vuelva a iniciarla. El
negativo de «cuenta vencida» con la elección de proveedor vigente (`codex_login_required`)
no se ejerció: el cierre de sesión revoca antes la elección. Faltan W2 y W3; A-21 sigue
parcial.

## W2 ejecutado (06-11-2026): pasado en W2a + W2c

W2 se dividió en dos casos encadenados de un lanzamiento cada uno, sobre la cuota y el
proveedor de W1 (`/private/tmp/jq-tqbfd_1m`) y ligados a `bdfd7434085a1c6ebf2aa94e5470e3fe263f059e`
(CI completa 42245415425 en verde; artefacto 13518505833 predescargado, SHA-256 `1260d839…`).

- **W2a** (admisión `7261b18b…`, preparado `139bf0ff…`, cierre GUI `3dc9c806…`, recibo
  `d04e288b…`, `passed_A21_W2a_case`). Perfil, código y app nuevos (`state-w2`, `source-w2`,
  `Jarvis-W2.app`). Root renombró «Prueba A-21 W2» antes del login; el dueño volvió a iniciar
  sesión. Un run de texto sin herramientas dio una palabra clave aleatoria y el modelo respondió
  exactamente «Anotado». Cierre sin logout. Lectura de sólo lectura posterior: la conversación
  quedó ligada al thread `01a1b19a-0bc5-7332-b15b-fb8829cd3ac6`. Un primer arranque se paró
  antes de cualquier efecto porque el bootstrap heredado exigía un campo visual; se corrigió y
  se volvió a congelar.
- **W2b quemado sin efectos** (admisión `33fb488a…`, preparado `446595a1…`, cierre
  `1ea187a5…`). Relanzó la misma app y perfil con la sesión conservada, pero su escenario leyó
  `reused_W2b_app_profile_and_copies` (clave renombrada por error del generador; la real es
  `reused_W2a_…`) y se detuvo sin intención ni recibo. Mientras se investigaba venció el plazo:
  un SIGTERM `native_active_deadline` a la app admitida, primer desmontaje fallido y segundo
  confirmado. No se envió ningún mensaje.
- **W2c** (admisión `50a4d79c…`, preparado `ae04258a…`, cierre GUI `1219e0f1…` sin señales ni
  incidencias, recibo `b2407b0f…`, `passed_A21_W2c_case`). Se encadenó al cierre por plazo de
  W2b y relanzó la **misma** app y el **mismo** perfil sin copias ni login: `service.status`
  conectado sin nuevo inicio de sesión. En la misma conversación se pidió la palabra clave sin
  repetirla; la respuesta fue la palabra exacta, sin herramientas. Antes de congelar, la
  validación en copia detectó otro renombrado indebido (`keyword_hidden_from_W2b_prompt`) y se
  comprobaron con los recibos reales todas las claves y condiciones de cadena.

Comprobación final de sólo lectura (rollout `7140799f…`): el binding sigue en el mismo thread,
no se creó rollout nuevo y ese rollout contiene los dos turnos («Anotado.» y la palabra clave).
Acredita sesión conservada, reanudación del binding propio y continuidad de contexto tras
reinicio de la app, sin `thread/list` ni threads ajenos. Falta W3; A-21 sigue parcial.
