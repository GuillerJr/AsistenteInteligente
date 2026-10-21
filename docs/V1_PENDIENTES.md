# Jarvis 1.0: revisión del proyecto y lista de pendientes

Revisión del 21-10-2026 sobre el commit `6ceee4e`, con la app instalada en
`~/Applications/Jarvis.app`. Esta lista sirve para continuar el trabajo con Codex
hasta publicar la primera versión. Las reglas de trabajo del repositorio están en
[`AGENTS.md`](../AGENTS.md).

Cada punto dice **qué pasa**, **dónde** está y **cuándo se da por terminado**.
Prioridades:

- **P0:** bloquea publicar la 1.0.
- **P1:** se nota al usarla y debería entrar en la 1.0.
- **P2:** calidad interna que conviene hacer antes de crecer.
- **P3:** después de la 1.0.

Marca cada punto como hecho (`[x]`) en el mismo commit que lo resuelve.

---

## 1. Qué hay hoy

| Área | Estado | Dónde |
| --- | --- | --- |
| Daemon Python (LangGraph, broker de herramientas, auditoría, memoria) | Funciona, NVIDIA exclusivo | `src/aegis_core` (≈84.000 líneas, 232 módulos) |
| App de barra de menús (SwiftUI, macOS 14+) | Funciona; dirección visual «Cálido» aplicada | `native/AegisAudio/Sources/AegisMenuBar` (≈16.000 líneas) |
| Núcleo nativo (voz, IPC firmada, control del Mac, VAD ONNX) | Funciona | `native/AegisAudio/Sources/AegisAudioCore` |
| Chat: historial cifrado, papelera, chat temporal, adjuntos, notas de reunión, chat lateral | Funciona | `JarvisChatView.swift`, `JarvisChatStore*.swift` |
| Modo plan, Dot (trabajo 24/7), tareas programadas, proyectos | Funciona; probado en vivo | `JarvisPlanCard`, `JarvisDotView`, `JarvisScheduledView`, `JarvisProjectView`, `dot.py`, `scheduling.py`, `projects.py` |
| Navegador incorporado | Funciona; «Preguntar a Jarvis sobre esta página» adjunta la página (comprobado en esta revisión) | `JarvisBrowserPanel.swift` |
| Complementos de Codex y Claude con autoinstalación aprobada | Funciona; probado en vivo | `plugins/foreign*.py`, `JarvisForeignPluginsView.swift` |
| Uso del Mac con aprobación por app | Probado solo con pruebas automáticas | `computer_app_grants.py`, `ActionReviewView.swift` |
| Integraciones MCP (Google Workspace, GitHub, archivos, Playwright, Home Assistant), Telegram, TV, Android, iPhone | Funcionan según su configuración | `src/aegis_core/mcp`, `telegram`, `tools` |
| CLI de ingeniería (`jarvis`) | Funciona | `src/aegis_core/cli.py` |
| Pruebas | Python: ≈3.800 pasan y 46 se omiten; ruff limpio. Swift: 367 pasan. CI en verde (el único fallo reciente fue falta de runner macOS en GitHub, no del código) | `tests/`, `native/AegisAudio/Tests` |
| Distribución | Firma local «Jarvis Local Development»; **Gatekeeper rechaza la app**; versión `0.1.0` (build 1) | `script/release_macos.sh`, `packaging/` |

---

## 2. P0: bloquean la 1.0

- [ ] **P0-1. Firma Developer ID y notarización.**
  - Hoy `codesign` muestra `Authority=Jarvis Local Development` sin TeamIdentifier, y `spctl -a` responde `rejected`. Cualquier otra persona verá la app bloqueada.
  - La cadena P12/P13/P14 ya existe (`script/p12_distribution_release_gate.sh`, `p13_…`, `p14_…`) y espera `AEGIS_CODESIGN_IDENTITY="Developer ID Application: …"` y `AEGIS_NOTARY_PROFILE`.
  - **Acción del dueño:** inscribirse en Apple Developer Program, crear el certificado y el perfil de `notarytool`. Codex no debe crear cuentas ni introducir credenciales.
  - **Hecho cuando:** `spctl -a -vv Jarvis.app` dice `accepted`, `source=Notarized Developer ID`, y `xcrun stapler validate` pasa.

- [ ] **P0-2. Reducir el tamaño de la app (427 MB).**
  - **Helpers:** pesan 130 MB. Cada uno (`jarvis-local-brain`, `jarvis-local-embedding`, `jarvis-spotlight-indexer`, `jarvis-ios-bridge`, `jarvis-biometric-calibrator`, `JarvisComputerHelper`) pesa unos 22 MB porque enlaza estáticamente `AegisAudioCore` completo, ONNX Runtime incluido.
    - Dividir `AegisAudioCore` en un núcleo sin ONNX y un target aparte para el VAD Silero (ONNX), que solo enlacen `Jarvis` y quien de verdad detecte voz. La alternativa es convertir el núcleo en un framework dinámico compartido dentro de `Contents/Frameworks`.
    - Ver `native/AegisAudio/Package.swift`.
  - **Daemon:** pesa 268 MB. `llvmlite` (108 MB), `scipy` (35 MB) y `mlx` (24 MB) llegan por `mlx-whisper`, que solo se usa en la transcripción de confirmación (`providers/mlx_provider.py`, `runtime/daemon.py:580`).
    - Pasar esa transcripción al reconocimiento de voz en el dispositivo de la app (ya existe `AegisAudioCore/SpeechTranscription.swift`) y sacar `mlx-whisper` de las dependencias del daemon y de `packaging/jarvis_daemon.spec`.
  - Revisar si `jarvis-local-brain` sigue haciendo falta con `inference_nvidia_only=True`. Si no hace falta, quitarlo del bundle, de `peer_identity.py` y de `BundledDaemonSupervisor`.
  - Después, bajar los presupuestos de `script/build_and_run.sh` y `script/rc3_rc8_gate.sh`. Hoy son 25 MiB por ejecutable y 450.560 KiB por bundle. El ejecutable `Jarvis` ya está en 25,68 MB, en el límite.
  - **Hecho cuando:** el bundle pesa menos de 200 MB y las pruebas, el pre-push y la voz siguen funcionando.

- [ ] **P0-3. Actualizaciones desde la app.**
  - Hoy actualizar exige ejecutar `jarvis-daemon secure-update-install … --confirm-install <revisión>` en Terminal (`docs/quality/P14_SECURE_UPDATE_QUALIFICATION.md`). No hay feed publicado ni botón en la app.
  - Publicar `Jarvis.update.json` y `Jarvis.zip` en un lugar fijo (por ejemplo GitHub Releases) y añadir en Ajustes › General una sección «Acerca de y actualizaciones»: versión, «Buscar actualizaciones» e «Instalar y reiniciar».
  - Reutilizar la verificación Ed25519, la protección contra downgrade y la recuperación de `secure_update.py`. La instalación sigue necesitando la confirmación explícita del dueño.
  - **Hecho cuando:** una 1.0.1 publicada se detecta y se instala desde la app, y una versión adulterada o más antigua se rechaza.

- [ ] **P0-4. Primer arranque guiado.**
  - No existe flujo de bienvenida. Un usuario nuevo tiene que descubrir solo dónde poner la clave NVIDIA (Ajustes › Modelos de IA), qué permisos dar y cómo hablarle.
  - Crear un asistente de 4–5 pasos que se muestre si falta la clave o es la primera ejecución:
    1. Bienvenida.
    2. Clave de NVIDIA, con enlace para obtenerla y prueba de conexión.
    3. Permisos opcionales, explicados uno a uno (micrófono, calendario, contactos, automatización, accesibilidad y grabación de pantalla solo si se activa el uso del Mac).
    4. Abrir al iniciar sesión.
    5. Un primer mensaje de ejemplo.
  - Debe poder saltarse y volver a abrirse desde Ajustes.
  - **Hecho cuando:** con una cuenta de macOS nueva y sin datos en `~/Library/Application Support/Aegis`, se llega a una primera respuesta sin abrir documentación.

- [ ] **P0-5. Instalar en `/Applications` y un solo mecanismo de inicio.**
  - Hay unas 30 rutas fijas a `~/Applications/Jarvis.app` en `src`, `native` y `script` (por ejemplo `config.py:84,134,151,212,224`). En modo bundle el supervisor las sobrescribe por variables de entorno, pero hay que comprobar que todo funciona si la app se arrastra a `/Applications`.
  - El inicio de sesión usa dos mecanismos:
    - el LaunchAgent `~/Library/LaunchAgents/ai.aegis.menubar.autostart.plist` que instala `script/menu_bar_service.sh`;
    - el interruptor de `JarvisGeneralSettingsView.swift`, que usa `SMAppService`.

    Hoy el interruptor aparece **apagado** aunque la app arranca sola. Para la versión distribuida hay que quedarse solo con `SMAppService` y migrar o eliminar el LaunchAgent heredado.
  - **Hecho cuando:** la app copiada a `/Applications` arranca, encuentra sus helpers y el interruptor refleja la realidad.

- [ ] **P0-6. Versión y «Acerca de».**
  - `pyproject.toml` y `CFBundleShortVersionString` dicen `0.1.0`, y `CFBundleVersion` dice `1`. Hay que poner `1.0.0` con build monotónico (la cadena P14 ya admite `AEGIS_BUILD_NUMBER`).
  - Añadir un `CHANGELOG.md` y una ventana «Acerca de Jarvis» en español con versión, build, revisión y licencias de terceros: Silero, ONNX Runtime y las dependencias Python del daemon, a partir de `dist/Jarvis.spdx.json`.

- [ ] **P0-7. Menús del sistema en español.**
  - La barra de menús muestra `About Jarvis`, `Edit`, `View`, `Window`, `Help`, `Hide Jarvis` y `Quit Jarvis` en inglés junto a «Configuración…» y «Conversación».
  - Añadir `CFBundleDevelopmentRegion = es` y `CFBundleLocalizations = [es]` a `native/AegisAudio/AppBundle/Info.plist`, más el recurso de localización que haga falta.
  - El menú «Help › Jarvis Help» no lleva a ninguna parte: debe abrir el manual (`MANUAL_FACIL.md` o una página publicada).

- [ ] **P0-8. Actualizar el alcance oficial de la 1.0.**
  - `packaging/JarvisV1Scope.json` todavía describe «local_conversation … usable when every remote provider is absent» y un «hybrid_brain» opcional. Eso contradice el diseño actual: NVIDIA exclusivo y sin respaldo local (ver el README).
  - Rehacerlo con las capacidades reales de la 1.0 y revisar qué pruebas lo leen (`rc3_rc8_gate.sh`, la calificación de distribución).

- [ ] **P0-9. Textos de privacidad que faltan.**
  - El daemon descubre televisores por SSDP y multicast (`tools/smart_tv.py`), conecta con Android por adb y hace sondeos TCP.
  - Desde macOS 15, eso necesita `NSLocalNetworkUsageDescription`, y quizá `NSBonjourServices`, en el `Info.plist` de la app que lanza el daemon.
  - Comprobar en un Mac limpio que el aviso aparece con texto en español y que la función sigue funcionando si se rechaza.

---

## 3. P1: funcionalidad, fallos y diseño para la 1.0

### Fallos vistos en esta revisión

- [ ] **P1-1. La ventana «Decisión de Jarvis» se queda abierta y vacía.** Tras resolver las decisiones queda una ventana de 560×428 con «Sin decisiones pendientes» (en otro Escritorio). Debe cerrarse sola cuando la cola queda vacía. La escena es `Window("Decisión de Jarvis", id: "approval")` en `AegisMenuBarApp.swift:160` y el estado vacío está en `ApprovalView.swift:21`.
- [ ] **P1-2. El menú de «Estilo» apareció abierto sobre otras secciones de Ajustes.** Al pasar de General a Diagnóstico, Modelos y Cuentas, la lista «Predeterminada / Directa / Cercana / Profesional / Divertida» siguió abierta encima del contenido hasta pulsar Esc. Reproducir y asegurar que un selector abierto se cierra al cambiar de sección (`JarvisMenuPicker.swift`, `JarvisGeneralSettingsView.swift`, `JarvisSettingsView.swift`).
- [ ] **P1-3. «Abre este enlace» intenta usar Chrome por CDP y falla con «No pude conectar a Chrome»** cuando Chrome no está conectado. Cuando no haya navegador de automatización, la respuesta natural es abrir la URL en el navegador incorporado o en el predeterminado. Revisar el enrutado en `orchestration/direct_actions.py`, `semantic_tools.py` y `tools/chrome_cdp.py`.
- [ ] **P1-4. Barra de direcciones del navegador incorporado.** Comprobar a mano que Enter navega y que buscar texto libre va a DuckDuckGo. El control remoto de la revisión no pudo enviar Enter. Ver `JarvisBrowserPanel.swift`.
- [ ] **P1-5. Probar en vivo «Permitir <App>» (en esta conversación / siempre).** Hay que pedir una acción en Notas, aprobarla con «Siempre», comprobar que la siguiente no pregunta y quitar el permiso en Ajustes › Memoria y privacidad. Hoy solo lo cubren `tests/test_computer_app_grants.py` y una prueba Swift de firma.

### Funciones que faltan en el chat

- [ ] **P1-6. Editar un mensaje propio y regenerar la respuesta.** Hoy cada mensaje solo tiene «Copiar mensaje» (`JarvisChatView.swift:379`). Añadir «Editar» (reenvía desde ese punto) y «Regenerar» en la última respuesta, como en Claude y ChatGPT.
- [ ] **P1-7. Valorar respuestas (👍/👎).** El backend ya guarda opiniones del dueño (ADR-0128 y ADR-0129). Exponerlo en cada respuesta.
- [ ] **P1-8. Más opciones en el menú «Conversación».** Hoy solo tiene «Abrir panel de Jarvis» y «Abrir chat». Añadir Nuevo chat (⌘N), Chat temporal, Modo plan, Buscar chats (⌘F), Abrir navegador, Archivar y Eliminar chat, con sus atajos.
- [ ] **P1-9. El título del chat se corta aunque hay espacio** («¿Cuál es la capital d…» junto a la etiqueta del proyecto). Dar prioridad de ancho al título en la cabecera de `JarvisChatView.swift`.
- [ ] **P1-10. Tareas programadas que necesitan aprobación.** Hoy se cancelan con «necesita tu aprobación» (`scheduling.py:545`). Para la 1.0, la aprobación debería quedar pendiente un tiempo y avisar con una notificación de macOS o por Telegram con un botón «Revisar», sin aprobar nunca por el dueño.

### Pasada visual pendiente

- [ ] **P1-11. Modo claro, popover de la barra de menús y ventanas pequeñas.** El estilo «Cálido» se revisó sobre todo en modo oscuro. Revisar con capturas:
  - el modo claro;
  - el popover de `MenuBarView.swift`;
  - la ventana de chat a 800 px de ancho con el navegador abierto;
  - Ajustes a su tamaño mínimo;
  - las tarjetas de aprobación.

  Las reglas están en `docs/JARVIS_INTERFACE_STYLE.md`.
- [ ] **P1-12. Accesibilidad.** Hay 62 `accessibilityLabel` en `AegisMenuBar`. Recorrer chat, Ajustes, aprobaciones y navegador con VoiceOver y con el teclado (Tab y Esc), y añadir etiqueta a todo botón de solo icono que aún no la tenga. Los de la cabecera del chat ya la tienen; revisar la barra lateral, el navegador, el popover y el HUD.
- [ ] **P1-13. Estados sin conexión y de error.** Comprobar con NVIDIA caído, sin red y con la clave revocada que el chat muestra un mensaje claro y un «Reintentar». Diagnóstico mostraba 4 fallos y 13 límites de frecuencia en 24 h.

---

## 4. P2: calidad interna

- [ ] **P2-1. Dividir `MenuBarModel.swift` (4.553 líneas).** Separar por responsabilidad: voz, chat y trabajos, aprobaciones, uso del Mac, presencia y HUD, ajustes. Usar extensiones o stores pequeños con `@Observable`, sin cambiar el comportamiento.
- [ ] **P2-2. Dividir `orchestration/graph.py` (4.406 líneas)** en nodos (`route`, `plan`, `specialist`, `synthesis`, `notes`).
  - Eliminar las ramas de `inference_nvidia_only=False` que el daemon ya no usa (el daemon siempre pasa `True`, `runtime/daemon.py:612`).
  - Retirar `brain/hybrid_client.py` y el resto del cerebro híbrido si nada instalado los usa. Moverlos a pruebas históricas o borrarlos.
- [ ] **P2-3. Pruebas de la capa de interfaz.** `AegisMenuBar` no tiene target de pruebas; las 367 pruebas Swift son del núcleo. Crear `AegisMenuBarTests` y cubrir la lógica pura: lectura de pasos de `JarvisPlanCard`, `JarvisChatStore` (paneles, proyectos, chats temporales), `JarvisScheduledTask` y `Date(jarvisISO:)`.
- [ ] **P2-4. 88 `except Exception` en `src`.** Revisarlos, dejar solo los de frontera (scheduler, IPC) y que registren `error_type`, nunca contenido.
- [ ] **P2-5. Documentación.**
  - El `README.md` pesa 125 KB y 1.764 líneas. Dejarlo en una portada corta: qué es, instalar, primeros pasos, enlaces.
  - Mover el detalle a `docs/` y unificar `MANUAL_FACIL.md` con el manual de instalación.
- [ ] **P2-6. Textos en un String Catalog.** Todos los textos de la interfaz son literales en español dentro de las vistas. Pasarlos a `Localizable.xcstrings` (español como base) para poder traducir más adelante.
- [ ] **P2-7. CI.**
  - `actions/checkout@v4` usa Node 20, que está obsoleto: pasar a `@v5`.
  - Los trabajos macOS arm64 a veces no consiguen runner. El 21-10 un trabajo de Python se quedó 15 minutos sin runner. Añadir reintento o aceptar el fallo de capacidad.
- [ ] **P2-8. Imágenes del bundle.** `JarvisTricksterDot.png` pesa 1,7 MB y `JarvisLogo.png` 1 MB. Comprimirlas o pasarlas a HEIC o al catálogo de assets sin perder calidad visible.

---

## 5. P3: después de la 1.0

- Historial de lo que Jarvis hizo en el Mac (estilo «Chronicle»), consultable y borrable.
- Interfaz en inglés (requiere P2-6).
- App compañera en iPhone en lugar del puente actual.
- Certificación física de voz P10/P11 en la build final, con la voz del dueño y en una sala ruidosa: es una prueba que hace el dueño, no Codex.

---

## 6. Limpieza de datos de prueba (la hace el dueño desde la app)

Durante el desarrollo quedaron en el Mac del dueño elementos que no forman parte del producto:

- el proyecto «Prueba de proyecto» y sus chats;
- varios chats «Prepara mi semana…», «Tarea: Prueba de Jarvis» y «Hola»;
- el complemento `commit-commands` y el marketplace `anthropics/claude-code`, en Ajustes › Extensiones › Codex y Claude.

No forman parte del repositorio. Codex no debe borrarlos por su cuenta.

---

## 7. Lista de salida de la 1.0

1. Todos los P0 marcados.
2. Pre-push completo en verde: ruff, pytest, `./script/test_native.sh`, bundle `--package`, `acceptance-benchmark`, `production-workflows` y `long-horizon-reliability`.
3. Cadena `p12 → p13 → p14` con Developer ID real y puntuación 100.
4. Instalación limpia en otra cuenta de macOS:
   - asistente de bienvenida;
   - una pregunta;
   - una acción con aprobación;
   - una tarea programada;
   - un proyecto;
   - el navegador;
   - la actualización a una 1.0.1 de prueba.
5. Etiqueta `v1.0.0` en `main` y publicación del ZIP notarizado, el manifiesto firmado y el `CHANGELOG.md`.
