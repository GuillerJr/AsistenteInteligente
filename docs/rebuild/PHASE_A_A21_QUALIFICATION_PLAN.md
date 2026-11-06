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
