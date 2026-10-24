# Jarvis 1.0: revisión del proyecto y lista de pendientes

Revisión del 21-10-2026 sobre el commit `6ceee4e`, con la app instalada en
`~/Applications/Jarvis.app`. Esta lista sirve para continuar el trabajo con Codex
hasta publicar la primera versión. Las reglas de trabajo del repositorio están en
[`AGENTS.md`](../AGENTS.md).

Los párrafos originales se conservan como contexto; el avance fechado de cada punto describe el estado actual. Una casilla abierta puede tener toda la implementación terminada y una certificación pendiente del dueño.

## Reconstrucción propia acordada el 24-10-2026

El dueño aclaró que la nueva variante se construye **en este repositorio**, con
código propio informado por el análisis de OpenClaw, sin convertir Jarvis en un
fork ni usar OpenClaw como runtime. El plan y los inventarios completos viven en
[`rebuild/README.md`](rebuild/README.md). Los pendientes históricos siguen siendo
criterios de paridad y no desaparecen al cambiar de arquitectura.

- [x] Registrar la decisión, los módulos backend/nativos y las herramientas que
  debe recuperar la nueva variante, distinguiendo código activo, condicionado y heredado.
- [x] Inventariar el árbol de referencia y ejecutar la primera parte estática con
  REA, conservando identidad, fuentes, Evidence IDs e incógnitas.
- [ ] Completar las partes restantes de la investigación de OpenClaw y contrastar
  los comportamientos relevantes con pruebas controladas. El análisis global es parcial.
- [ ] **Fase A:** terminar y verificar primero la variante base propia: sesiones,
  ejecución, permisos, extensibilidad, proveedor elegido, persistencia y app de
  escritorio nativa SwiftUI con ventana, Dock, barra de menús y atajo.
  Los adaptadores actuales de sus pruebas son sintéticos.
- [ ] **Fase B posterior:** implementar las herramientas específicas de Jarvis y
  conectarlas a recorridos reales con aprobación, resultado, cancelación y revocación.
  Reconstruir y verificar cada capacidad de ambos inventarios antes de sustituir
  e instalar el producto nuevo. El prototipo offline no acredita esa sustitución.

Cada punto dice **qué pasa**, **dónde** está y **cuándo se da por terminado**.
Prioridades:

- **P0:** bloquea publicar la 1.0.
- **P1:** se nota al usarla y debería entrar en la 1.0.
- **P2:** calidad interna que conviene hacer antes de crecer.
- **P3:** después de la 1.0.

Marca cada punto como hecho (`[x]`) en el mismo commit que lo resuelve.

---

La verificación del lote inicial (3.934 Python, 371 Swift del núcleo y 48 XCTest de interfaz), junto con las regresiones posteriores, se documenta en [quality/V1_IMPLEMENTATION_VERIFICATION.md](quality/V1_IMPLEMENTATION_VERIFICATION.md).

Las mejoras funcionales y de memoria del 23-10, sus pruebas y límites se documentan
en [quality/ARCHITECTURE_IMPROVEMENTS_20261023.md](quality/ARCHITECTURE_IMPROVEMENTS_20261023.md).

La revisión con REA del 22-10 y el refuerzo de herramientas, borradores,
recuperación y proyectos se documentan en
[quality/CODEX_REA_TOOL_REVIEW.md](quality/CODEX_REA_TOOL_REVIEW.md), con cobertura
y límites explícitos. No sustituye las comprobaciones manuales que siguen abiertas.

## Cambio de inferencia del 23-10-2026

Por indicación del dueño, se retira la exclusividad NVIDIA. La primera conexión es
Codex con ChatGPT mediante el cliente oficial, con sesión propia y herramientas
sujetas al broker de Jarvis. Los embeddings usan Apple local; voz y notas de audio
quedan sin configurar. Ver [ADR-0235](architecture/ADR-0235-configured-inference-and-codex-chatgpt.md).

- [x] Inicio de sesión del dueño y prueba de conexión desde la app firmada: verificados
  el 23-10 con la revisión `291d0c6`. También pasaron texto, visión sintética y una
  lectura real `system_power_status` con resultado verificado.
- [x] Recorrido TextEdit «Siempre», uso en otro chat y revocación con Codex, verificado
  el 23-10 con la app firmada `36bcc60`. Ver P1-5 y la evidencia de la conexión.
- [ ] Añadir una línea al final de un documento en TextEdit: ampliar la acción y su
  verificación para conservar el texto previo y acreditar el salto y la posición.
  El contrato actual bloquea saltos de línea en `type`; reconocer «añade» sin esa
  implementación no basta. La escritura literal y los permisos ya están comprobados.
- [ ] Incorporar una alternativa de audio y volver a certificar la voz.
- [ ] Ampliar proveedores (local con Ollama, OpenAI API o Anthropic) en cambios posteriores
  según la elección del dueño, sin respaldos automáticos.

La cobertura y sus límites están en [quality/CODEX_CONNECTION_20261023.md](quality/CODEX_CONNECTION_20261023.md).
Los párrafos NVIDIA anteriores describen pruebas históricas, no la conexión activa.

## 0. Segunda revisión (22-10-2026, commit `46e8b75`)

Hecha con Claude Code sobre la app instalada, que coincide con `46e8b75` (versión 1.0.0, build 214865395).

**Comprobado y correcto:**
- Python: 4.633 pruebas pasan y 46 se omiten (las de diálogo NVIDIA real, que piden activación expresa). Ruff limpio.
- Swift: 389 pruebas del núcleo y 202 de la interfaz.
- CI en verde en `main`.
- Tamaño: la app pasó de 427 a 310 MB.
  - El ejecutable `Jarvis` pesa 5,8 MB (antes 24).
  - Los helpers pesan unos 2 MB cada uno; ONNX vive solo en `jarvis-vad-helper` (20 MB).
  - `jarvis-local-brain` salió del bundle.
  - El daemon sigue en 268 MB por `mlx-whisper` (punto P3).
- Menús del sistema en español; Ayuda abre el «Manual de Jarvis».
- Menú Conversación completo.
- Chat temporal de prueba: respondió «42». Las respuestas tienen copiar, regenerar, 👍 y 👎, y los mensajes propios tienen «Editar desde este mensaje».
- Ajustes › General › «Acerca de y actualizaciones»: «Buscar actualizaciones» responde «Todavía no hay una actualización publicada en el canal estable», que es correcto porque el repositorio es público y aún no tiene releases.
- `NSLocalNetworkUsageDescription` presente.
- `MenuBarModel.swift` quedó en 337 líneas y `graph.py` en 732.

**Hallazgos nuevos (resolver antes de la 1.0):**
- [x] **R2-1. Prueba intermitente en la interfaz.** **Resuelto el 22-10:** la prueba volvía a asignar la misma vista y SwiftUI no estaba obligada a redibujarla. Ahora simula el cambio de chat con una identidad de borrador nueva, como la app real, y pasó 6 de 6 ejecuciones completas.
  - `ChatComposerKeyboardTests.testDraftBindingAndConversationReplacementClearPreviousUndo` (`Tests/AegisMenuBarTests/ChatComposerTests.swift:148-150`) falló en 1 de 3 ejecuciones completas de `./script/test_native.sh`: «Deshacer» recuperó el borrador de otro chat. Sola pasa siempre.
  - Puede ser un problema real del gestor de deshacer compartido o del foco entre pruebas. Hay que aislarlo; si no, el pre-push fallará al azar.
  - **Hecho cuando:** 10 ejecuciones completas seguidas pasan.
- [x] **R2-2. Reabierto P1-1.** **Resuelto el 22-10 al eliminar la ventana (ver R2-15).** La ventana «Decisión de Jarvis» seguía existiendo, vacía («Sin decisiones pendientes»), en otro Escritorio con la build actual. El cierre solo ocurre cuando `interactionID` pasa a `nil` (`AegisMenuBarApp.swift:240`). Si la ventana se abre o se restaura sin decisión pendiente, nadie la cierra. Cerrarla también al aparecer sin decisión y desactivar la restauración de esa escena.
- [x] **R2-3. Recorrido de acceso al Mac por app (P1-5).** **Verificado el 23-10 con Codex y `36bcc60`:** el dueño eligió «Siempre»; en otro chat temporal se escribió el literal de prueba sin nueva aprobación y con resultado verificado. Tras quitar el permiso, volvió a aparecer la aprobación y no cambió el documento. Esta comprobación de TextEdit no certifica todas las aplicaciones ni todas las instrucciones posibles; las incidencias históricas se conservan en P1-5.
- [ ] **R2-4. Estado vacío de «Archivados».** Con la lista vacía muestra «Tu primera conversación · Empieza un chat para verlo aquí», el mismo texto que Recientes. Debe decir que no hay chats archivados y cómo archivar uno.
- [ ] **R2-5. «Buscar chats» se queda resaltado** en la barra lateral mientras se ven Recientes, Archivados o la Papelera, y convive con el campo «Buscar en títulos y mensajes» justo debajo. Unificar: un solo buscador y sin resaltar cuando no está activo.
- [x] **R2-6. Diagnóstico muestra códigos internos.** **Resuelto el 23-10:** los fallos conocidos tienen explicación en español y conservan el código en el detalle técnico. Diagnóstico también avisa del último respaldo NVIDIA y muestra el estado real del índice semántico. Regresiones Python y de presentación Swift. «Fallos recientes» enseñaba `incomplete_model_response` sin explicación específica.
- [ ] **R2-7. Peso del repositorio.**
  - Los informes de cobertura REA (`docs/quality/CODEX_REA_*_COVERAGE.json`, unas 50.000 líneas y 1,4 MB) se versionan junto a la documentación; el paquete git pasó de 14 a 17 MB en un día.
  - Dejar en el repositorio el resumen `.md` y mover los JSON a un artefacto de CI o a `dist/` (ignorado).
  - `Localizable.xcstrings` y `es.lproj/Localizable.strings` contienen los mismos textos. Confirmar que el `.strings` se genera siempre desde el catálogo (`docs/LOCALIZACION.md`) y que una prueba falla si se desincronizan.
- [x] **R2-8. El historial de chats y los proyectos están vacíos** en el Mac del dueño, papelera incluida. **Resuelto el 22-10:** el dueño confirmó que los borró él (limpieza del punto 6); no hubo pérdida de datos.
- [ ] **R2-9. Auditar los borrados de chats y proyectos (P2).** Al revisar R2-8 no había forma de distinguir un borrado del dueño de una pérdida de datos, porque la auditoría no registra esos borrados. Registrar solo metadatos (tipo, cuántos, cuándo; nunca títulos ni contenido) al eliminar, vaciar la papelera o borrar un proyecto.

- [x] **R2-10. Una aprobación por voz se concedió sola (P0 de seguridad).**
  - **Qué pasó:** el 22-10 a las 20:39, en una prueba, Jarvis creó un borrador en Gmail 5 segundos después de mostrar la tarjeta. Nadie lo aprobó.
  - **Por qué:**
    - Cada tarjeta abría el micrófono 3 segundos.
    - El modelo de voz solo distingue entre fondo y «guiller», así que toma cualquier voz por la del dueño.
    - El reconocedor de Apple tiene «sí» y «ok» como pistas.
    - Con una sola palabra bastaba.
  - **Resuelto el mismo día:**
    - La aprobación por voz está desactivada por defecto, con un interruptor en Ajustes › Voz.
    - El daemon rechaza la voz en toda acción que no sea de solo lectura (`voice_confirmation_refused`), incluidos el uso del Mac y los flujos de varios pasos.
    - La app no abre el micrófono si el daemon no marca `voice_approval_available`.
    - La respuesta posterior a una acción ya no puede negarla: si el modelo dice «no puedo…», Jarvis usa un recibo fijo («Creé el borrador en Gmail. No lo envié…»).
  - **Pendiente:**
    - Sustituir el clasificador de dos clases por una verificación de hablante real.
    - Quitar las pistas «sí/ok» del reconocedor.
    - Exigir una frase que nombre la acción.
- [x] **R2-11. Los modelos elegidos en Ajustes no gobiernan las decisiones.**
  - **Resuelto el 23-10:** intención, herramientas, interpretación de memoria, novedad y objetivos usan la selección por rol de Ajustes, sin sobreescrituras fijas. El último respaldo exitoso identifica modelo solicitado, modelo usado y motivo, sin contenido del usuario. Guardar modelos sigue indicando que requiere reiniciar el servicio. Pruebas con preferencias distintas y transportes simulados; el recorrido sintético real confirmó el modelo del enrutador.
  - La selección de herramientas, la intención y la interpretación de tareas usan `INTERPRETATION_MODEL_IDS` (`models.py:23`, Lightning y GPT-OSS 20B), que ignora Ajustes.
  - Al configurar DeepSeek V4 Pro, ninguna llamada lo usó y no hubo aviso. Probablemente falló y Jarvis cayó al respaldo en silencio.
  - **Hecho cuando:** esas decisiones usan el modelo configurado y Diagnóstico avisa cuando un modelo cae a su respaldo.
- [x] **R2-12. La búsqueda web no devuelve nada.**
  - **Resuelto el 23-10:** desafíos anti-robots, HTML no reconocido y RSS ambiguamente vacío se notifican como fallos del proveedor. Se mantienen los vacíos explícitos y el respaldo seguro. Una consulta pública real mediante el broker obtuvo una fuente verificable; 31 nuevas regresiones cubren fallos, respaldo, resultados vacíos y diagnóstico sin consultas ni URL.
  - Desde este Mac, `html.duckduckgo.com` responde 202 con una página anti-robots y el RSS de Bing llega vacío (`tools/web.py:257-258`).
  - `web_research` termina «con éxito» con 68 bytes y Jarvis responde «No encontré resultados públicos».
  - **Hecho cuando:** una búsqueda real devuelve fuentes, o el fallo del proveedor se informa como error y no como «sin resultados».
- [x] **R2-13. Elección de herramientas con el modelo actual.**
  - **Resuelto el 23-10:** las solicitudes de redactar en el chat, confirmar o trabajar sobre una lista pegada no habilitan acciones en servicios; el destino explícito TextEdit conserva el control del Mac. El selector recibe hasta 12 turnos dentro de 8 KiB y respeta el modelo configurado. Cinco escenarios sintéticos reales pasaron, incluyendo el control positivo de lectura de correo; ninguna herramienta se ejecutó. El recorrido con aprobación real de R2-3 sigue pendiente y no queda certificado por estas pruebas.
  - En una batería fija con chats temporales, Jarvis propuso herramientas equivocadas en 4 de 7 turnos:
  - programar una tarea ante «solo confírmame que lo anotaste»;
  - buscar en Gmail para ordenar una lista que estaba en el chat;
  - crear un archivo en Drive o un borrador ante «escribe un mensaje»;
  - Drive en vez de TextEdit.

  Se resolverá en parte con R2-11. También hay que revisar los atajos de `direct_actions.py`, ampliar el contexto (hoy 12 mensajes y 16 KB, y el selector de herramientas solo ve 2 mensajes) y no ofrecer herramientas de escritura cuando la petición solo pide redactar.
- [x] **R2-15. Decisiones como en Codex y Claude.** **Hecho el 22-10:**
  - Ya no aparece Touch ID automáticamente.
  - **Se eliminó la ventana «Decisión de Jarvis»** con sus diálogos.
  - Toda decisión (aprobar una acción, responder una pregunta, elegir navegador, un aviso de que la tarea se detuvo) es una tarjeta (`JarvisDecisionCard`) en el lugar donde se hizo la petición:
    - **En el chat:** dentro de la conversación, con sus detalles desplegables y los botones.
    - **Por voz o a Dot, o con el chat oculto:** la misma tarjeta en negro bajo el notch (`NotchApprovalPanelController`). Sin notch, aparece centrada bajo la barra de menús.
  - El doble clic en Dot y los botones del menú y del HUD vuelven a mostrar la tarjeta donde corresponda.
  - Si el dueño activó la voz, la tarjeta indica «También puedes decir «Aprobado»» en acciones de solo lectura.
  - Desaparece «Corregir…»: como en Codex, se rechaza y se escribe la petición corregida.
- [ ] **R2-16. Detalles legibles en las tarjetas de decisión.** Los «Detalles» de una tarea programada muestran los campos en bruto: `at: 2026-10-22T22:32`, `every minutes: null`, `kind: once`, `reminder: false`. El presentador de la vista previa (`job_tool_presenter.py`, `confirmation_preview`) debe dar etiquetas en español, omitir los campos vacíos y formatear las fechas.
- [x] **R2-14. Al rechazar una acción** (Resuelto el 22-10: ya no hay ventana y la lista se actualiza al resolver la decisión.), la ventana «Decisión de Jarvis» sigue abierta (ver R2-2) y la barra lateral mantiene «Requiere tu atención 1».

**Sigue pendiente del dueño:** P0-1 (Developer ID y notarización), y con él P0-2 (publicar la primera actualización y probar 1.0.0 → 1.0.1), además del recorrido con VoiceOver de P1-12.

## 1. Estado de la revisión original (antes de completar este lote)

| Área | Estado | Dónde |
| --- | --- | --- |
| Daemon Python (LangGraph, broker de herramientas, auditoría, memoria) | Funciona, NVIDIA exclusivo | `src/aegis_core` (≈84.000 líneas, 232 módulos) |
| App de barra de menús (SwiftUI, macOS 14+) | Funciona; dirección visual «Cálido» aplicada | `native/AegisAudio/Sources/AegisMenuBar` (≈16.000 líneas) |
| Núcleo nativo (voz, IPC firmada, control del Mac, VAD ONNX) | Funciona | `native/AegisAudio/Sources/AegisAudioCore` |
| Chat: historial cifrado, papelera, chat temporal, adjuntos, notas de reunión, chat lateral | Funciona | `JarvisChatView.swift`, `JarvisChatStore*.swift` |
| Modo plan, Dot (trabajo 24/7), tareas programadas, proyectos | Funciona; probado en vivo | `JarvisPlanCard`, `JarvisDotView`, `JarvisScheduledView`, `JarvisProjectView`, `dot.py`, `scheduling.py`, `projects.py` |
| Navegador incorporado | Funciona; «Preguntar a Jarvis sobre esta página» adjunta la página (comprobado en esta revisión) | `JarvisBrowserPanel.swift` |
| Complementos de Codex y Claude con autoinstalación aprobada | Funciona; probado en vivo | `plugins/foreign*.py`, `JarvisForeignPluginsView.swift` |
| Uso del Mac con aprobación por app | TextEdit «Siempre», otro chat y revocación comprobados en la app firmada; otras apps según su compatibilidad | `computer_app_grants.py`, `ActionReviewView.swift` |
| Integraciones MCP (Google Workspace, GitHub, archivos, Playwright, Home Assistant), Telegram, TV, Android, iPhone | Funcionan según su configuración | `src/aegis_core/mcp`, `telegram`, `tools` |
| CLI de ingeniería (`jarvis`) | Funciona | `src/aegis_core/cli.py` |
| Pruebas | Python: ≈3.800 pasan y 46 se omiten; ruff limpio. Swift: 367 del núcleo y 21 de la interfaz pasan | `tests/`, `native/AegisAudio/Tests` |
| Distribución | Firma local «Jarvis Local Development»; **Gatekeeper rechaza la app**; versión `0.1.0` (build 1) | `script/release_macos.sh`, `packaging/` |

---

## 2. P0: bloquean la 1.0

- [ ] **P0-1. Firma Developer ID y notarización.**
  - Hoy `codesign` muestra `Authority=Jarvis Local Development` sin TeamIdentifier, y `spctl -a` responde `rejected`. Cualquier otra persona verá la app bloqueada.
  - La cadena P12/P13/P14 ya existe (`script/p12_distribution_release_gate.sh`, `p13_…`, `p14_…`) y espera `AEGIS_CODESIGN_IDENTITY="Developer ID Application: …"` y `AEGIS_NOTARY_PROFILE`.
  - **Acción del dueño:** inscribirse en Apple Developer Program, crear el certificado y el perfil de `notarytool`. Codex no debe crear cuentas ni introducir credenciales.
  - **Hecho cuando:** `spctl -a -vv Jarvis.app` dice `accepted`, `source=Notarized Developer ID`, y `xcrun stapler validate` pasa.

- [ ] **P0-2. Actualizaciones desde la app.**
  - **Avance del 21-10 (implementación y verificación):** Implementado: Ajustes › General muestra versión y canal estable, verifica manifiesto Ed25519, hash, build y revisión, y exige confirmación explícita para instalar. Incluye límites de descarga, recuperación y revalidación de inactividad. Falta la publicación notarizada y la prueba 1.0.0 → 1.0.1, dependientes de P0-1 y de la aprobación del dueño.
  - Hoy actualizar exige ejecutar `jarvis-daemon secure-update-install … --confirm-install <revisión>` en Terminal (`docs/quality/P14_SECURE_UPDATE_QUALIFICATION.md`). No hay feed publicado ni botón en la app.
  - Publicar `Jarvis.update.json` y `Jarvis.zip` en un lugar fijo (por ejemplo GitHub Releases) y añadir en Ajustes › General una sección «Acerca de y actualizaciones»: versión, «Buscar actualizaciones» e «Instalar y reiniciar».
  - Reutilizar la verificación Ed25519, la protección contra downgrade y la recuperación de `secure_update.py`. La instalación sigue necesitando la confirmación explícita del dueño.
  - **Hecho cuando:** una 1.0.1 publicada se detecta y se instala desde la app, y una versión adulterada o más antigua se rechaza.

- [x] **P0-3. Primer arranque guiado.**
  - **Avance del 21-10 (implementación y verificación):** Implementado el asistente de cinco pasos, reapertura desde Ajustes, clave segura, conexión, permisos opcionales e inicio de sesión. El ejemplo no reinicia el daemon ni interrumpe voz, grabaciones o aprobaciones; solo termina tras un envío válido. Cubierto con XCTest. El dueño confirmó la primera respuesta tras completar la bienvenida en la cuenta nueva «Jarvis Pruebas», con la copia firmada de `ff0e7be` sin modelos personales de voz. Es una validación manual reportada por el dueño; la comprobación del aviso TCC de red local sigue separada en P0-8.
  - No existe flujo de bienvenida. Un usuario nuevo tiene que descubrir solo dónde poner la clave NVIDIA (Ajustes › Modelos de IA), qué permisos dar y cómo hablarle.
  - Crear un asistente de 4–5 pasos que se muestre si falta la clave o es la primera ejecución:
    1. Bienvenida.
    2. Clave de NVIDIA, con enlace para obtenerla y prueba de conexión.
    3. Permisos opcionales, explicados uno a uno (micrófono, calendario, contactos, automatización, accesibilidad y grabación de pantalla solo si se activa el uso del Mac).
    4. Abrir al iniciar sesión.
    5. Un primer mensaje de ejemplo.
  - Debe poder saltarse y volver a abrirse desde Ajustes.
  - **Hecho cuando:** con una cuenta de macOS nueva y sin datos en `~/Library/Application Support/Aegis`, se llega a una primera respuesta sin abrir documentación.

- [x] **P0-4. Instalar en `/Applications` y un solo mecanismo de inicio.**
  - **Avance del 21-10 (implementación y verificación):** Probada la copia firmada en `/Applications`: arranca su daemon, mantiene IPC seguro y NVIDIA configurado, y ejecuta el VAD real con PCM sintético. Se restauró la instalación habitual al terminar. Las rutas son relativas al bundle y SMAppService es el único mecanismo de inicio; se migra el LaunchAgent heredado conservando la preferencia y el interruptor refleja enabled/requiresApproval/disabled. La aprobación que macOS pueda pedir sigue siendo del dueño.
  - Hay unas 30 rutas fijas a `~/Applications/Jarvis.app` en `src`, `native` y `script` (por ejemplo `config.py:84,134,151,212,224`). En modo bundle el supervisor las sobrescribe por variables de entorno, pero hay que comprobar que todo funciona si la app se arrastra a `/Applications`.
  - El inicio de sesión usa dos mecanismos:
    - el LaunchAgent `~/Library/LaunchAgents/ai.aegis.menubar.autostart.plist` que instala `script/menu_bar_service.sh`;
    - el interruptor de `JarvisGeneralSettingsView.swift`, que usa `SMAppService`.

    Hoy el interruptor aparece **apagado** aunque la app arranca sola. Para la versión distribuida hay que quedarse solo con `SMAppService` y migrar o eliminar el LaunchAgent heredado.
  - **Hecho cuando:** la app copiada a `/Applications` arranca, encuentra sus helpers y el interruptor refleja la realidad.

- [x] **P0-5. Versión y «Acerca de».**
  - **Avance del 21-10 (implementación y verificación):** Resuelto: versión 1.0.0, build monotónico, Acerca de en español con revisión, CHANGELOG y licencias generadas desde el SBOM y dependencias instaladas.
  - `pyproject.toml` y `CFBundleShortVersionString` dicen `0.1.0`, y `CFBundleVersion` dice `1`. Hay que poner `1.0.0` con build monotónico (la cadena P14 ya admite `AEGIS_BUILD_NUMBER`).
  - Añadir un `CHANGELOG.md` y una ventana «Acerca de Jarvis» en español con versión, build, revisión y licencias de terceros: Silero, ONNX Runtime y las dependencias Python del daemon, a partir de `dist/Jarvis.spdx.json`.

- [x] **P0-6. Menús del sistema en español.**
  - **Resuelto el 21-10:** región y localización `es` en el bundle, recurso `es.lproj`, «Acerca de Jarvis» y «Manual de Jarvis». El manual viaja en el bundle y se abre desde Ayuda; contratos de empaquetado comprobados.
  - La barra de menús muestra `About Jarvis`, `Edit`, `View`, `Window`, `Help`, `Hide Jarvis` y `Quit Jarvis` en inglés junto a «Configuración…» y «Conversación».
  - Añadir `CFBundleDevelopmentRegion = es` y `CFBundleLocalizations = [es]` a `native/AegisAudio/AppBundle/Info.plist`, más el recurso de localización que haga falta.
  - El menú «Help › Jarvis Help» no lleva a ninguna parte: debe abrir el manual (`MANUAL_FACIL.md` o una página publicada).

- [x] **P0-7. Actualizar el alcance oficial de la 1.0.**
  - **Resuelto el 21-10:** 13 capacidades actuales, contrato NVIDIA obligatorio sin respaldo local y gate RC3–RC8 con `nvidia_only`. Las pruebas rechazan capacidades híbridas y promesas de inferencia sin red. Ver ADR-0234.
  - `packaging/JarvisV1Scope.json` todavía describe «local_conversation … usable when every remote provider is absent» y un «hybrid_brain» opcional. Eso contradice el diseño actual: NVIDIA exclusivo y sin respaldo local (ver el README).
  - Rehacerlo con las capacidades reales de la 1.0 y revisar qué pruebas lo leen (`rc3_rc8_gate.sh`, la calificación de distribución).

- [x] **P0-8. Textos de privacidad que faltan.**
  - **Verificación final del 21-10:** En la cuenta principal, por petición del dueño, se probó la app firmada `0262a63` desde un chat temporal con el historial oculto. El dueño confirmó que apareció el aviso real de macOS y pulsó «No permitir». Codex observó el error controlado de descubrimiento local y después «El resultado es 4» ante «¿Cuánto es 2 más 2?». El bundle instalado contiene la explicación de red local en español. La instalación, firma estricta, diagnóstico del daemon/IPC y VAD sintético también pasan. No se reiniciaron permisos ni se cambió de usuario.
  - **Seguimiento del 21-10:** La prueba manual detectó que «Busca televisores en mi red local. Solo descubre dispositivos; no enciendas ni cambies nada» acababa en una búsqueda web. Se corrigió el enrutado hacia `smart_tv_discover`, con respuesta local validada y sin control ni encendido de dispositivos. Pasan 463 pruebas enfocadas, incluidas 66 regresiones nuevas. Por indicación del dueño, las siguientes pruebas en vivo se harán únicamente en su cuenta principal; no se reinician permisos TCC. El aviso y su rechazo efectivo todavía requieren comprobación.
  - **Avance del 21-10 (implementación y verificación):** Texto de red local presente y denegación controlada al abrir/configurar/enviar/recibir SSDP, Wake-on-LAN y sondeos TCP IPv4/IPv6. Se añadieron 34 regresiones. Falta observar el aviso TCC real en una cuenta limpia; no se conceden permisos por el dueño.
  - **Avance del 21-10:** añadido `NSLocalNetworkUsageDescription` en español. Sigue pendiente comprobar el aviso y la denegación en una cuenta limpia; no se declaran servicios Bonjour que el código no usa.
  - El daemon descubre televisores por SSDP y multicast (`tools/smart_tv.py`), conecta con Android por adb y hace sondeos TCP.
  - Desde macOS 15, eso necesita `NSLocalNetworkUsageDescription`, y quizá `NSBonjourServices`, en el `Info.plist` de la app que lanza el daemon.
  - Comprobar en un Mac limpio que el aviso aparece con texto en español y que la función sigue funcionando si se rechaza.

---

## 3. P1: funcionalidad, fallos y diseño para la 1.0

### Fallos vistos en esta revisión

- [x] **P1-1. La ventana «Decisión de Jarvis» se queda abierta y vacía.** (Resuelto: la ventana ya no existe, ver R2-15.) Tras resolver las decisiones queda una ventana de 560×428 con «Sin decisiones pendientes» (en otro Escritorio). Debe cerrarse sola cuando la cola queda vacía. La escena es `Window("Decisión de Jarvis", id: "approval")` en `AegisMenuBarApp.swift:160` y el estado vacío está en `ApprovalView.swift:21`. **Resuelto:** cierre desde el observador persistente, comprobación inicial y cancelación del enfoque diferido; pruebas con ventanas AppKit sintéticas.
- [x] **P1-2. El menú de «Estilo» apareció abierto sobre otras secciones de Ajustes.** Al pasar de General a Diagnóstico, Modelos y Cuentas, la lista «Predeterminada / Directa / Cercana / Profesional / Divertida» siguió abierta encima del contenido hasta pulsar Esc. Reproducir y asegurar que un selector abierto se cierra al cambiar de sección (`JarvisMenuPicker.swift`, `JarvisGeneralSettingsView.swift`, `JarvisSettingsView.swift`). **Resuelto:** se cancela el seguimiento al cambiar sección, desmontar el selector o quitarlo de la ventana. XCTest cubre cancelación y desmontaje.
- [x] **P1-3. «Abre este enlace» intenta usar Chrome por CDP y falla con «No pude conectar a Chrome»** cuando Chrome no está conectado. Cuando no haya navegador de automatización, la respuesta natural es abrir la URL en el navegador incorporado o en el predeterminado. Revisar el enrutado en `orchestration/direct_actions.py`, `semantic_tools.py` y `tools/chrome_cdp.py`. **Resuelto:** abrir una URL explícita usa el navegador predeterminado aunque se ofrezca CDP; «este enlace» usa una fuente reciente del daemon y mantiene confirmación. Las tareas compuestas conservan la ruta de automatización. Pruebas del router y del broker pasan.
- [x] **P1-4. Barra de direcciones del navegador incorporado.** Comprobar a mano que Enter navega y que buscar texto libre va a DuckDuckGo. El control remoto de la revisión no pudo enviar Enter. Ver `JarvisBrowserPanel.swift`.
  - **Avance del 21-10 (implementación y verificación):** La dirección acepta URL HTTP(S), dominio o búsqueda DuckDuckGo. Los esquemas externos requieren una confirmación nativa explícita; nunca se abren automáticamente. Pruebas del campo real envían Enter sin red y verifican el destino.
- [x] **P1-5. Probar en vivo «Permitir <App>» (en esta conversación / siempre).** Recorrido de «Siempre», reutilización entre chats y revocación completado con TextEdit; el alcance por conversación conserva su cobertura y las comprobaciones previas.
  - **Verificación real con Codex y `36bcc60` (23-10):** el dueño eligió «Siempre» y apareció el recibo de permiso permanente. El intento inicial se detuvo por `user_takeover` y el documento seguía vacío. En otro chat temporal, tras resolver un fallo transitorio de conexión, Jarvis escribió exactamente «PRUEBA JARVIS CODEX A» sin otra aprobación; se observó el texto y la evaluación técnica marcó `computer_use`, `succeeded=true`, `outcome_verified=true`. El dueño quitó TextEdit en Ajustes y cerró esa pantalla privada. La nueva petición «En TextEdit, escribe…» volvió a mostrar la aprobación; el documento conservó solo A y se detuvo la solicitud sin autorizarla. «En TextEdit, añade…» reveló una variante todavía no reconocida por el enrutador; no se confunde esa negativa con una comprobación del permiso. La cancelación de una síntesis concurrente también expuso una carrera del transporte, reproducida aparte con tareas sintéticas. Ver [evidencia de conexión](quality/CODEX_CONNECTION_20261023.md).
  - **Prueba real con `90b1b70` (23-10):** Instalación limpia, firma, diagnóstico y VAD sintético aprobados. El dueño concedió «Siempre»: apareció el recibo, el evento de creación indicó `always` y se comprobó el permiso persistente de TextEdit. Tras una detención por actividad del teclado o puntero, un reintento escribió A y verificó la postcondición en dos pasos; TextEdit contenía el literal. En chats temporales nuevos se reutilizó el permiso sin aprobación, pero B no se escribió: hubo un bloqueo anterior a actuar, una propuesta inválida y una respuesta incompleta. El diagnóstico de bloqueo todavía agrupaba rechazo del modelo y fallo de enlace AX bajo `no_progress`; no acredita la causa exacta. Tres sondeos con AX ficticia completaron dos escrituras B conservando A y reprodujeron una respuesta incompleta cuando Omni devolvió HTTP 503 y el respaldo NVIDIA Muse agotó 256 tokens. Esta reproducción no identifica retrospectivamente el modelo del turno real fallido. Se detienen los reintentos en la interfaz hasta aislar las causas. Siguen pendientes la escritura B verificada entre chats y la revocación; esta casilla permanece abierta.
  - **Tras instalar `8d79bd1`:** Firma, arranque, diagnóstico, VAD sintético, 4.780 pruebas Python, 599 Swift, gates y CI aprobados. La ejecución dejó de fallar por respuesta incompleta de visión, pero el documento seguía vacío con un resultado parcial. El dueño eligió «En esta conversación»; apareció el recibo y el siguiente intento reutilizó el permiso sin preguntar. No se acredita «Siempre». Un ciclo completo con NVIDIA y AX ficticio reprodujo dos `focus` sobre un campo ya enfocado: se detuvo con `no_progress`, un paso y ninguna escritura. Se conserva esa barrera y se corrigen las instrucciones y el contexto del paso anterior. Se añaden motivos de detención al mensaje parcial y diagnósticos por enum, sin contenido de pantalla, a la auditoría. La segunda instalación y el recorrido real de «Siempre» y revocación siguen pendientes.
  - **Seguimiento del 23-10 sobre `74911e3`:** En un chat temporal sin historial visible y un documento vacío, «en el documento nuevo y vacío de TextEdit» todavía permitió proponer Drive; no se aprobó esa operación. Las propuestas nativas inválidas se bloquearon. Dos aprobaciones llegaron al ejecutor, pero fallaron con `ComputerVisionNotDispatchedError` antes de actuar; TextEdit siguió vacío. El dueño indicó que eligió «Siempre», pero no hubo recibo ni permiso persistente guardado: esa elección todavía no se considera acreditada. Se amplía el reconocimiento del destino y se aporta al planificador un ejemplo de postcondición ligado al literal solicitado, conservando todas las validaciones. El sondeo NVIDIA de las dos frases corregidas produjo propuestas que requieren confirmación, sin ejecutar herramientas. Sigue pendiente repetir la escritura, reutilización y revocación en la app corregida.
  - **Seguimiento de `893291f` y revisión REA del 22-10:** Se verificó la escritura sintética «PRUEBA JARVIS C». Los intentos posteriores terminaron con respuesta incompleta de NVIDIA; el alcance completo sigue sin demostrarse en vivo. Se conserva ahora el aviso de recibo de permiso aunque falle el modelo, se registran estados sin contenido privado y se distingue un fallo anterior a actuar de una acción incierta. Las regresiones automáticas no cierran esta casilla.
  - **Seguimiento de `b442202`:** Instalación, firma, diagnóstico, VAD y CI aprobados. El dueño seleccionó el permiso permanente y apareció su confirmación. TextEdit recibió exactamente «PRUEBA JARVIS A», pero Jarvis respondió que no podía verificar el resultado. Se reproduce otra diferencia de contrato: la etiqueta AX juntaba el identificador y la descripción del rol, mientras la postcondición exige el nombre exacto. Se separa una etiqueta estable del descriptor completo usado para seguridad. En otro chat se rechazó una propuesta y el siguiente intento terminó con respuesta incompleta de NVIDIA, sin escribir; la reutilización con ejecución verificada todavía no se declara completada. Se solicitó al dueño quitar el permiso para continuar con la revocación y el alcance por conversación.
  - **Seguimiento del 22-10 tras reinstalar `fc4b4f6`:** El dueño eligió «En esta conversación», pero el control visual se detuvo por contenido sensible y TextEdit quedó vacío. La misma petición volvió a pedir aprobación y se rechazó. Se reprodujo con datos sintéticos un falso positivo: el valor «Borrar» de los controles de color invalidaba toda la observación. Se omiten esos botones del contexto exportado y se mantiene su bloqueo como destinos de acciones; las credenciales, pagos y campos seguros siguen bloqueando la observación, incluso sin etiqueta o geometría. La interfaz confirma ahora si la respuesta firmada acredita que se guardó el alcance solicitado. Las nuevas pruebas de IPC verifican conservación del permiso tras bloqueo/cancelación, aislamiento entre conversaciones, «Siempre» y revocación. No reproducen una pérdida del permiso; el síntoma real sigue pendiente de comprobar con el aviso nuevo.
  - **Seguimiento tras reinstalar `06025bb`:** La aprobación nativa de TextEdit ya aparece con el objetivo completo y el campo solicitado. Se observaron caducidades sin ejecución y una detención por intervención del teclado o puntero; el documento de prueba seguía vacío. Otro reintento produjo una promesa sin ejecutar. Una regresión del grafo reproduce una vía posible: una propuesta denegada llegaba al sintetizador sin su denegación. Ahora esa vía responde de forma determinista que no ejecutó la acción y requiere una propuesta válida. No se afirma que esa fuera la causa exacta del turno real. Los permisos y su revocación siguen pendientes de completar en vivo.
  - **Seguimiento tras reinstalar `ad53037`:** La instalación firmada, diagnóstico, VAD sintético y CI pasaron. La prueba dejó de proponer Drive, pero respondió con opciones de confirmación en el chat sin proponer la herramienta. Se exige ahora la propuesta `computer_use` para una orden explícita de interacción con una app; esto no concede autorización, que sigue correspondiendo al dueño mediante la aprobación nativa. El recorrido completo de permisos continúa abierto.
  - **Seguimiento del 21-10:** La prueba en la cuenta principal, con un documento nuevo de TextEdit, detectó una propuesta incorrecta de crear un archivo en Drive. Se rechazó sin ejecutarla. Se corrige el alcance de las herramientas para la interacción explícita con una app y se conserva el objetivo completo, incluidas sus restricciones, al validarlo. La comprobación en vivo de conversación, «Siempre» y revocación sigue pendiente; esta corrección no la sustituye.
  - **Avance del 21-10 (implementación y verificación):** Se conservan las pruebas de grants, revocación, alcance y firma. La aprobación en vivo «Siempre» requiere una acción expresa del dueño y no se ha simulado en su cuenta.

### Funciones que faltan en el chat

- [x] **P1-6. Editar un mensaje propio y regenerar la respuesta.** Hoy cada mensaje solo tiene «Copiar mensaje» (`JarvisChatView.swift:379`). Añadir «Editar» (reenvía desde ese punto) y «Regenerar» en la última respuesta, como en Claude y ChatGPT.
  - **Avance del 21-10 (implementación y verificación):** Editar y regenerar crean una bifurcación desde el turno elegido, conservando la conversación original. Se conservan documentos; las imágenes no persistidas deben adjuntarse de nuevo. Pruebas del historial cifrado, IPC y estado del chat.
- [x] **P1-7. Valorar respuestas (👍/👎).** El backend ya guarda opiniones del dueño (ADR-0128 y ADR-0129). Exponerlo en cada respuesta.
  - **Avance del 21-10 (implementación y verificación):** Valoración positiva/negativa por respuesta, persistida en el historial cifrado y reflejada en las métricas del job; controles y contratos probados.
- [x] **P1-8. Más opciones en el menú «Conversación».** Hoy solo tiene «Abrir panel de Jarvis» y «Abrir chat». Añadir Nuevo chat (⌘N), Chat temporal, Modo plan, Buscar chats (⌘F), Abrir navegador, Archivar y Eliminar chat, con sus atajos.
  - **Avance del 21-10 (implementación y verificación):** Menú Conversación con nuevo chat, temporal, plan, búsqueda, navegador, archivar y papelera, más atajos. Archivar tiene estado propio y no equivale a eliminar.
- [x] **P1-9. El título del chat se corta aunque hay espacio** («¿Cuál es la capital d…» junto a la etiqueta del proyecto). Dar prioridad de ancho al título en la cabecera de `JarvisChatView.swift`. **Resuelto:** el título tiene prioridad dentro de su fila y la etiqueta del proyecto se limita a una línea; la revisión visual general continúa en P1-11.
- [x] **P1-10. Tareas programadas que necesitan aprobación.** Hoy se cancelan con «necesita tu aprobación» (`scheduling.py:545`). Para la 1.0, la aprobación debería quedar pendiente un tiempo y avisar con una notificación de macOS o por Telegram con un botón «Revisar», sin aprobar nunca por el dueño.
  - **Avance del 21-10 (implementación y verificación):** La aprobación permanece pendiente con vencimiento y notificación macOS «Revisar». Telegram conserva dos pasos explícitos y vincula propietario/chat/mensaje/digest/TTL. No se aprueba automáticamente; se evita solapamiento y se cancela al cerrar el servicio.

### Pasada visual pendiente

- [x] **P1-11. Modo claro, popover de la barra de menús y ventanas pequeñas.** El estilo «Cálido» se revisó sobre todo en modo oscuro. Revisar con capturas:
  - **Avance del 21-10 (implementación y verificación):** Vistas sintéticas de chat con navegador a 800 px, Ajustes mínimos, popover y aprobaciones en claro y oscuro. El historial se oculta cuando faltan columnas y desplaza proyectos/pendientes; el chat lateral respeta el ancho mínimo.
  - el modo claro;
  - el popover de `MenuBarView.swift`;
  - la ventana de chat a 800 px de ancho con el navegador abierto;
  - Ajustes a su tamaño mínimo;
  - las tarjetas de aprobación.

  Las reglas están en `docs/JARVIS_INTERFACE_STYLE.md`.
- [ ] **P1-12. Accesibilidad.** Hay 62 `accessibilityLabel` en `AegisMenuBar`. Recorrer chat, Ajustes, aprobaciones y navegador con VoiceOver y con el teclado (Tab y Esc), y añadir etiqueta a todo botón de solo icono que aún no la tenga. Los de la cabecera del chat ya la tienen; revisar la barra lateral, el navegador, el popover y el HUD.
  - **Avance del 21-10 (implementación y verificación):** Se añadieron nombres a los controles de icono pendientes y pruebas del árbol de accesibilidad y de teclado Enter/Tab/Mayús-Tab/Esc con datos sintéticos. El recorrido auditivo completo con VoiceOver queda identificado como comprobación manual adicional; no se confunde con la inspección automatizada del árbol AX.
- [x] **P1-13. Estados sin conexión y de error.** Comprobar con NVIDIA caído, sin red y con la clave revocada que el chat muestra un mensaje claro y un «Reintentar». Diagnóstico mostraba 4 fallos y 13 límites de frecuencia en 24 h.
  - **Avance del 21-10 (implementación y verificación):** Mensajes claros para falta de red, fallos de NVIDIA, credencial revocada y límites del proveedor; «Reintentar» conserva el contenido. Pruebas de recuperación y respuestas de error.

### Tamaño

- [x] **P1-14. Una sola copia de ONNX Runtime (libera el límite del ejecutable).**
  - **Avance del 21-10 (implementación y verificación):** ONNX queda en el único jarvis-vad-helper firmado, con PCM por pipes acotados y validación de ruta, identidad y certificado. Se retiraron los motores locales de inferencia del paquete. El ejecutable principal queda holgadamente bajo 25 MiB; la inferencia VAD se prueba con silencio sintético.
  - El peso de la app (427 MB) no viene de las herramientas. El código propio de Jarvis ocupa pocos MB; casi todo son copias repetidas de librerías.
  - El problema práctico: el ejecutable `Jarvis` ocupa 25,68 MB de un límite de 25 MiB (26,2 MB) que impone el pre-push en `script/build_and_run.sh` y `script/rc3_rc8_gate.sh`. Unos 20 MB son ONNX Runtime, y las pantallas nuevas (bienvenida, editar mensajes…) van a chocar con ese límite.
  - Cada helper (`jarvis-local-brain`, `jarvis-local-embedding`, `jarvis-spotlight-indexer`, `jarvis-ios-bridge`, `jarvis-biometric-calibrator`, `JarvisComputerHelper`) pesa unos 22 MB porque enlaza estáticamente todo `AegisAudioCore`, ONNX incluido. El único que no lo enlaza, `jarvis-speaker-trainer`, pesa 144 KB.
  - Dividir `AegisAudioCore` en un núcleo sin ONNX y un target aparte para el VAD Silero, que solo enlace quien detecte voz. La alternativa es convertir el núcleo en un framework dinámico compartido dentro de `Contents/Frameworks`. Ver `native/AegisAudio/Package.swift`.
  - Revisar si `jarvis-local-brain` sigue haciendo falta con `inference_nvidia_only=True`. Si no hace falta, quitarlo del bundle, de `peer_identity.py` y de `BundledDaemonSupervisor`.
  - **Hecho cuando:** ONNX aparece una sola vez en el bundle, el ejecutable `Jarvis` queda claramente por debajo de su límite, los helpers pesan unos pocos MB y la detección de voz sigue funcionando.

---

## 4. P2: calidad interna

- [x] **P2-1. Dividir `MenuBarModel.swift` (4.553 líneas).** Separar por responsabilidad: voz, chat y trabajos, aprobaciones, uso del Mac, presencia y HUD, ajustes. Usar extensiones o stores pequeños con `@Observable`, sin cambiar el comportamiento.
  - **Avance del 21-10 (implementación y verificación):** MenuBarModel queda en unas 337 líneas con 19 extensiones por responsabilidad. Se conservaron los contratos de voz, trabajos, chat, aprobaciones y presencia.
- [x] **P2-2. Dividir `orchestration/graph.py` (4.406 líneas)** en nodos (`route`, `plan`, `specialist`, `synthesis`, `notes`).
  - **Avance del 21-10 (implementación y verificación):** Grafo dividido por estado, enrutamiento, planificación, ejecución, síntesis, notas y memoria. La entrada productiva exige NVIDIA; clientes híbridos/locales se trasladaron a tests/historical y los motores locales salieron de la distribución. Whisper se conserva únicamente para transcripción, según el alcance P3.
  - Eliminar las ramas de `inference_nvidia_only=False` que el daemon ya no usa (el daemon siempre pasa `True`, `runtime/daemon.py:612`).
  - Retirar `brain/hybrid_client.py` y el resto del cerebro híbrido si nada instalado los usa. Moverlos a pruebas históricas o borrarlos.
- [x] **P2-3. Completar las pruebas de la capa de interfaz.** Ya existe `AegisMenuBarTests`: cubre el envío con adjuntos y modo plan, el cierre del chat lateral durante IPC y la conservación de intervalos al editar tareas. **Completado el 21-10:** lectura de la primera lista del plan, fechas ISO con zona y fracciones, cambios de panel, selección de proyecto, bloqueo durante operaciones, títulos y eliminación diferida de chats temporales. Se limpian los marcadores del plan al cambiar de chat y la selección de proyecto es atómica. Tras el lote actual, la suite de interfaz suma 48 pruebas XCTest; incluye además ventanas y selectores.
- [x] **P2-4. 88 `except Exception` en `src`.** Revisarlos, dejar solo los de frontera (scheduler, IPC) y que registren `error_type`, nunca contenido.
  - **Avance del 21-10 (implementación y verificación):** Auditadas las 88 capturas originales; las interiores se estrecharon y las fronteras restantes registran solo error_type. Inventario y justificación en EXCEPTION_AUDIT.md, con regresiones para privacidad y cancelación.
- [x] **P2-5. Documentación.**
  - **Avance del 21-10 (implementación y verificación):** README reducido a portada, detalle preservado en docs/REFERENCIA_TECNICA.md y manuales antiguos archivados. MANUAL_FACIL.md enlaza al único manual vigente y el bundle incluye una copia autónoma.
  - El `README.md` pesa 125 KB y 1.764 líneas. Dejarlo en una portada corta: qué es, instalar, primeros pasos, enlaces.
  - Mover el detalle a `docs/` y unificar `MANUAL_FACIL.md` con el manual de instalación.
- [x] **P2-6. Textos en un String Catalog.** Todos los textos de la interfaz son literales en español dentro de las vistas. Pasarlos a `Localizable.xcstrings` (español como base) para poder traducir más adelante.
  - **Avance del 21-10 (implementación y verificación):** String Catalog con español base y extracción tipada del compilador, recurso es.lproj para SwiftPM y script de sincronización. Prueba de bundle, números y texto del usuario como argumento, sin convertirlo en clave traducible. Ver LOCALIZACION.md.
- [x] **P2-7. CI.**
  - **Avance del 21-10 (implementación y verificación):** checkout@v5, workflow_dispatch y política explícita para indisponibilidad de runners, con repetición del mismo SHA cuando vuelva la capacidad. Ver quality/CI_CAPACITY.md.
  - `actions/checkout@v4` usa Node 20, que está obsoleto: pasar a `@v5`.
  - Los trabajos macOS arm64 a veces no consiguen runner. El 21-10 un trabajo de Python se quedó 15 minutos sin runner. Añadir reintento o aceptar el fallo de capacidad.
- [x] **P2-8. Imágenes del bundle.** `JarvisTricksterDot.png` pesa 1,7 MB y `JarvisLogo.png` 1 MB. Comprimirlas o pasarlas a HEIC o al catálogo de assets sin perder calidad visible.
  - **Avance del 21-10 (implementación y verificación):** Optimizadas las dos imágenes PNG sin pérdida: mismos píxeles RGBA, dimensiones y metadatos. Ahorro total de 185.825 bytes.

---

## 5. P3: después de la 1.0

- [x] Historial de las herramientas usadas por Jarvis, consultable y borrable.
  - **Avance del 22-10:** Ventana «Historial de acciones» (⌥⌘H), filtro por conversación, páginas de hasta 30 entradas y retención de 1.000. Guarda solo metadatos, omite chats temporales y requiere confirmación firmada para borrar una entrada o vaciarlo. Los fallos de reintentos y los resultados sin confirmar se distinguen del éxito informado. El borrado no deshace acciones ni elimina la auditoría de seguridad. Se añadieron regresiones de permisos, reinicio, paginación, resultados tardíos y fallo de escritura después del reemplazo. Ver [la cuarta revisión REA](quality/CODEX_REA_FEATURES.md), que también incorpora búsqueda en mensajes y pestañas del navegador.
- Daemon más ligero. Unos 167 MB de sus 268 MB (`llvmlite` 108, `scipy` 35, `mlx` 24) llegan por `mlx-whisper`. Solo se usa en la transcripción de confirmación (`providers/mlx_provider.py`, `runtime/daemon.py:580`). Pasarla al reconocimiento de voz en el dispositivo de la app (`AegisAudioCore/SpeechTranscription.swift`) y quitar `mlx-whisper` de las dependencias y de `packaging/jarvis_daemon.spec`. Con eso y P1-14, la app bajaría de unos 427 MB a menos de 200 MB, y las actualizaciones serían más ligeras.
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
