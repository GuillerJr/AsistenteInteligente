# Inventario nativo de Jarvis para la reconstrucción propia

> Snapshot histórico del producto anterior. El código referido fue retirado del
> árbol de trabajo al iniciar la base limpia. Sus estados describen la revisión
> histórica indicada, no capacidades disponibles en el núcleo nuevo. Consultar
> [CLEAN_START.md](CLEAN_START.md) y el historial Git `c97ed89`.

Fecha: 24-10-2026. Base observada: `4122a6e2e4ed5160abf2fc4480e752e6ab779197`.

Este catálogo registra el producto que la nueva variante propia debe poder recuperar.
Su destino es este repositorio; no supone modificar ni incorporar el código de OpenClaw.
El identificador técnico `aegis` se conserva en paquetes, bundle IDs, IPC y Keychain.
El catálogo de herramientas y servicios Python se complementa en
[`JARVIS_BACKEND_INVENTORY.md`](JARVIS_BACKEND_INVENTORY.md).

## Alcance y lectura de los estados

Se inventariaron los archivos del paquete SwiftPM, las superficies de la app, contratos
IPC consultados, auxiliares y empaquetado. Se leyeron los puntos de entrada y contratos
citados. El apéndice de archivos permite comprobar que un módulo no se pierda al dividir
la reconstrucción; no equivale a una auditoría semántica de cada línea de cada archivo.

- **Implementado:** existe una ruta de código y su contrato; no implica prueba real universal.
- **Configuración:** necesita permisos, cuenta, dispositivo, modelo de entrenamiento o servicio.
- **Evidencia real acotada:** hay una comprobación documentada de un recorrido concreto.
- **Pendiente:** comportamiento o certificación todavía abierto en `docs/V1_PENDIENTES.md`.
- **Histórico:** permanece en fuentes o documentación, pero no describe la ruta activa.

En esta revisión no se abrió la interfaz, no se ejecutó la app ni sus auxiliares, no se
solicitaron permisos y no se leyeron credenciales, conversaciones o datos del dueño.
La existencia de pruebas se registra como evidencia de contrato, no como prueba en vivo.

## Producto y superficies que deben conservarse

Las rutas de esta tabla parten de `native/AegisAudio/Sources/`.

| ID | Módulo o herramienta de producto | Rutas de referencia | Estado que debe trasladarse | Contrato para la nueva variante |
| --- | --- | --- | --- | --- |
| N01 | App de barra de menús, ciclo de vida y supervisión del daemon | `AegisMenuBar/App/AegisMenuBarApp.swift`, `AegisAudioCore/BundledDaemonSupervisor.swift`, `Models/MenuBarModel+Runtime.swift` dentro de `AegisMenuBar` | Implementado | Una app controla el proceso empaquetado; arranque comprobable, apagado que drena el daemon, reinicio sin procesos duplicados y fallo visible si no arranca. Cerrar ventanas no termina la app. |
| N02 | Ventanas, Dock y enfoque | `AegisMenuBar/App/RequestedWindowFocus.swift`, `JarvisDockController.swift`, `QuickEntryPanelController.swift`, `SideChatPanel.swift` dentro de `AegisMenuBar/Views` | Implementado; regresiones de ciclo de ventanas | Ventanas identificadas y tamaños mínimos; reapertura coherente; ninguna ventana de aprobación vacía/restaurada. Una entrada rápida no debe perder el borrador ni capturar actividad en segundo plano. |
| N03 | Chat de texto y estado del compositor | `AegisMenuBar/Views/JarvisChatView.swift`, `JarvisChatComposerView.swift`, `JarvisComposerTextEditor.swift`; `AegisMenuBar/Models/ChatSubmission.swift`, `JarvisChatStore.swift`, `JarvisChatStore+Drafts.swift` | Implementado | Envío, cola, parciales, detener, reintentar con adjuntos/borrador conservados, límites previos al IPC, identidad de respuesta ligada al chat correcto y estados honestos de error/cancelación. |
| N04 | Historial de conversaciones, búsqueda y organización | `AegisMenuBar/Models/JarvisChatStore+History.swift`, `JarvisChatStore+Search.swift`, `JarvisSidebarHistory.swift`; `Views/JarvisChatHistoryView.swift` | Implementado; R2-4/R2-5/R2-9 abiertos | Recientes, archivados, papelera, búsqueda y paginación; archivos temporales no convertidos en permanentes por accidente; eliminación con recibo y metadatos de auditoría sin contenido. Pendientes: texto vacío de archivados y buscador de la barra lateral. |
| N05 | Chat temporal y privacidad de contexto | `AegisMenuBar/Models/JarvisChatStore.swift`, `JarvisChatStore+History.swift`, `JarvisChatWorkspace.swift`; `Views/JarvisChatContextView.swift` | Implementado; usado en pruebas reales acotadas | Aviso de conversación temporal, sin memoria ni guardado permanente; contexto de proyecto desactivado en temporal; limpieza diferida segura y ninguna restauración cruzada de texto/adjuntos entre conversaciones. |
| N06 | Edición, regeneración, bifurcación, copia y valoración | `AegisMenuBar/Models/JarvisChatStore+Actions.swift`; `Views/JarvisChatActionsView.swift`, `JarvisChatResponseView.swift`; `AegisAudioCore/ChatResponseDocument.swift` | Implementado | Editar/regenerar conserva el original mediante bifurcación; solo turnos completos y persistidos tienen identidad válida; pedir de nuevo imágenes originales no guardadas; 👍/👎 asociado a la respuesta exacta. |
| N07 | Adjuntos, imágenes y transcripciones de reunión | `AegisAudioCore/ChatAttachment.swift`, `LocalImage.swift`; `AegisMenuBar/Views/JarvisChatAttachmentsView.swift`; `Services/ImageFilePicker.swift` | Implementado; texto y visión sintética comprobados con Codex | Texto/PDF/imagen con límites y validación; bytes/selección pertenecen al borrador actual; ninguna recuperación silenciosa de adjuntos antiguos; las imágenes no pasan a un historial permanente por defecto. |
| N08 | Proyectos y conversaciones de proyecto | `AegisMenuBar/Models/ProjectConversationStore.swift`, `JarvisChatWorkspace.swift`; `Views/JarvisProjectView.swift` | Implementado; configuración/datos propios del dueño | Crear, editar, seleccionar y eliminar proyecto; paginar todos sus chats; selección atómica; proyecto de un chat nuevo sin perder borrador; conservación de estado ante corrupción o respuestas obsoletas. |
| N09 | Modo plan y contexto del trabajo | `AegisMenuBar/Views/JarvisPlanCard.swift`, `JarvisChatContextView.swift`; `Models/ChatSubmission.swift`, `JarvisChatWorkspace.swift` | Implementado | Plan legible con pasos; modo plan investiga y propone sin ejecutar cambios; selección explícita y estado visible; metadatos del daemon siguen siendo autoridad. |
| N10 | Navegador incorporado y pestañas | `AegisMenuBar/Models/JarvisBrowserStore.swift`, `JarvisBrowserTabs.swift`, `JarvisBrowserPageCapture.swift`; `Views/JarvisBrowserPanel.swift`, `JarvisBrowserTabStrip.swift` | Implementado; captura de página documentada; no certificación de todos los sitios | WKWebView, URL/búsqueda, atrás/adelante/recargar, hasta ocho pestañas, enlaces del chat, nueva pestaña y cierre; «Preguntar a Jarvis» adjunta la página seleccionada con identidad vigente. Cambiar pestaña/ocultar/cerrar invalida capturas en curso. Esquemas externos requieren decisión explícita. |
| N11 | Chat lateral y espacio de trabajo | `AegisMenuBar/Models/SideChatStore.swift`, `JarvisChatWorkspace.swift`; `Views/SideChatPanel.swift`, `JarvisChatContextView.swift` | Implementado | Paneles de navegador, contexto y chat lateral con estado propio; una respuesta pendiente no se escribe en un panel cerrado o sustituido; selección de proyecto y adjuntos permanece consistente. |
| N12 | Dot, objetivos y tareas programadas | `AegisMenuBar/Views/JarvisDotView.swift`, `JarvisScheduledView.swift`; `Models/MenuBarModel+Scheduled.swift`, `JarvisScheduledExecution.swift`; `Services/ScheduledNotificationRouter.swift` | Implementado; recorridos previos documentados; pendientes de aprobación dependen del dueño | Vista Ahora/Próximo/Resultados/Atención, pausa/reanudación de Dot, listar/editar/habilitar/ejecutar/borrar tareas y objetivos; recibo de inicio verdadero; notificación lleva al trabajo exacto y nunca aprueba la acción. |
| N13 | Tarjetas de decisión y aprobación | `AegisMenuBar/Views/JarvisDecisionCard.swift`, `JarvisApprovalCard.swift`; `Models/MenuBarModel+Approvals.swift`, `MenuBarModel+Interactions.swift`, `ActionReviewPresentation.swift`; `App/NotchApprovalPanelController.swift` | Implementado; TextEdit «Siempre»/otro chat/revocación comprobados; R2-16 abierto | Una misma decisión en chat o notch/HUD; rechazo, caducidad y cancelación visibles; alcance una vez/conversación/siempre explícito; recibo solo tras respuesta firmada. Pendiente: detalles de tareas legibles y localizados. |
| N14 | Permisos por herramienta y por app, revocación | `AegisMenuBar/Views/JarvisPrivacySettingsView.swift`; `Services/JarvisSettingsStore.swift`; `AegisAudioCore/ApprovalSigner.swift`, `LocalIPC.swift` | Implementado; evidencia real limitada a TextEdit | Firmas distintas para cada método y alcance; el texto del modelo no concede permiso. Permiso permanente persiste hasta revocación y el alcance conversacional no migra a otros chats. Revocar debe impedir una nueva ejecución antes de preguntar. |
| N15 | Historial de acciones y borrado | `AegisMenuBar/Models/JarvisActionHistory.swift`, `MenuBarModel+ActionHistory.swift`; `Views/JarvisActionHistoryView.swift`; `AegisAudioCore/LocalIPC.swift` | Implementado | Consulta/paginación/filtro por conversación, estado verificado frente a fallido/cancelado, metadatos mínimos y borrado firmado ligado a revisión. No usar títulos de conversaciones ni argumentos como telemetría pública. |
| N16 | Uso real del Mac: observación y acciones | `JarvisComputerHelper/main.swift`; `AegisAudioCore/ComputerControl.swift`, `QuietEventDispatcher.swift`, `UserSessionExecutionGate.swift`; `AegisMenuBar/Services/ComputerControlService.swift`, `Models/MenuBarModel+Computer.swift` | Implementado con TCC; TextEdit literal/permisos comprobados; nueva línea al final pendiente | `status`, `activate`, `capture`, `act`; acciones `click`, `focus`, `replace_text`, `type`, `key`, `scroll`. ScreenCaptureKit y AX/OCR describen el objetivo; los efectos se vinculan a observación, proceso, ventana y estado de sesión. Ceder ante intervención física; cancelar no equivale a no haber escrito un prefijo. No certificar posición/preservación solo por contener texto. |
| N17 | Visión bajo demanda, ventana y contexto de aplicación | `AegisMenuBar/Services/ScreenCaptureService.swift`; `AegisAudioCore/WindowCapture.swift`, `OnDemandVisionCapture.swift`, `ActiveVisionSensor.swift`, `LocalVoiceApplicationContext.swift`; `Models/MenuBarModel+VoiceCapture.swift` | Implementado; permisos/configuración | Distinguir observación para actuar de captura adjunta solicitada por el dueño. Capturar solo la ventana/proceso autorizado y su pantalla, sin audio ni cursor; rechazo de apps y campos sensibles; validar de nuevo TCC y sesión antes/después. |
| N18 | Captura de voz, transcripción y medidores | `AegisAudioCore/SpeechTranscription.swift`, `SpeechInputProcessor.swift`, `MicrophoneMeter.swift`, `AudioMeter.swift`, `AcousticSensor.swift`, `VoiceActivity.swift`; `AegisMenuBar/Models/MenuBarModel+Voice*.swift`; `AegisAudioHelper/main.swift` | Implementado local; TCC y assets de idioma; ruta hablada completa sin configurar | Push-to-talk, final de turno, límites, feedback parcial local, manejo de silencio y errores. Audio crudo no se convierte en logs/transcripts IPC; el envío de un turno no acepta parciales como finales. Ninguna captura automática por solo abrir la app. |
| N19 | VAD y aislamiento ONNX | `AegisVADRuntime/SileroVADRuntime.swift`, `Resources/silero-vad.onnx`; `JarvisVADHelper/main.swift`; `AegisAudioCore/VADProcessClient.swift`, `SileroVADDetector.swift` | Implementado; self-test sintético empaquetado | Una sola copia de ONNX Runtime, auxiliar separado firmado, protocolo acotado, inferencia finita y recuperación ante caída. Conservación de licencia/notice del modelo. Un self-test de silencio no acredita reconocimiento de voz real. |
| N20 | Palabra de activación y entrenamiento | `AegisAudioCore/WakeWord*.swift`, `FollowUpListeningGate.swift`; `JarvisWakeWordTrainer/main.swift`; `AegisMenuBar/Views/WakeWordEnrollmentView.swift`, `Models/MenuBarModel+WakeWord*.swift` | Implementado; opt-in y entrenamiento del dueño | Activación local «Jarvis», detector y ventana de escucha, entrenamiento de muestras, capacidad/estado visibles, pausa durante TTS y otras capturas, recuperación sin activar sesiones suspendidas. No transferir muestras o modelos privados como código. |
| N21 | Identidad de voz, presencia y autorización múltiple | `AegisAudioCore/SpeakerIdentity.swift`, `SpeakerEnrollment.swift`, `SpeakerModelTraining.swift`, `LocalVoiceSpeakerIdentity.swift`, `DualChannelAuthorizer.swift`, `OwnerPresenceLease.swift`; `JarvisSpeakerTrainer/main.swift`, `JarvisBiometricCalibrator/main.swift`; `Services/OwnerPresenceAuthenticator.swift` | Implementado; enrolamiento/permisos/decisiones del dueño | Una puntuación de hablante no equivale a consentimiento. Ventanas y desafíos acotados; firmas/identidades ligadas a interacción actual; no aceptar TTS propio, voz repetida o prueba sintética como aprobación real. La regresión R2-10 sigue siendo criterio obligatorio. |
| N22 | Síntesis de voz, reproducción, interrupción y repetir | `AegisAudioCore/SpeechOutput.swift`, `SpeechArtifact.swift`, `SpeechStreamChunker.swift`, `SpeechAudioReplayBuffer.swift`, `SpeechPlayoutPolicy.swift`, `SpeechPlaybackOutcome.swift`, `LocalVoiceReplay.swift`; `AegisMenuBar/Models/MenuBarModel+VoiceOutput.swift` | Infraestructura implementada; **proveedor de audio productivo sin configurar** | Streaming/audio con integridad, secuencia, formato y límites; cancelación coordinada; reproducción completa frente a parcial; preservar texto cuando falta voz. El login ChatGPT del chat no acredita síntesis/transcripción remotas. No reactivar NVIDIA ni introducir respaldo silencioso. |
| N23 | Notas de reunión | `AegisAudioCore/MeetingTranscriber.swift`; `AegisMenuBar/Models/MenuBarModel+Meetings.swift`; `ChatAttachment.swift` | Implementado; micrófono/Speech y prueba real pendiente por alcance | Inicio/parada explícitos, texto en vivo, transcripción como adjunto y resumen con temas/decisiones/tareas; exclusión mutua con wake word; mensajes claros cuando no hay voz o falla captura. No grabar reuniones por iniciativa de la app. |
| N24 | Presencia visual, mascota, notch y HUD | `AegisMenuBar/App/Notch*`, `HUDPanelController.swift`, `MascotAutonomyController.swift`, `JarvisPointerController.swift`; `Views/Notch*`, `HUD*`, `JarvisNotchMascot.swift`; `AegisAudioCore/Mascot*.swift`, `AssistantPresentation.swift`, `AssistantPanelLayout.swift` | Implementado; fixtures visuales | Estados coherentes con el trabajo real; animación, energía, hover/expansión y accesibilidad respetan reducción de movimiento/transparencia. El puntero ilustrativo no cuenta como ejecución. Fixtures no abren micrófono ni daemon. |
| N25 | Alertas proactivas, energía, sesión y temperatura | `AegisMenuBar/Services/ProactiveEventMonitor.swift`, `ThermalMonitor.swift`; `Models/MenuBarModel+Presence.swift`, `MenuBarModel+Runtime.swift`; `AegisAudioCore/RuntimePowerStatus.swift`, `ProactiveAlertGate.swift`, `UserSessionExecutionGate.swift` | Implementado; configuración y permisos según sensor | Batería/red/agenda/rendimiento, avisos acotados, no spam; cierre de sesión/bloqueo/suspensión detienen capturas y efectos; reanudar exige permiso de ejecución vigente y no revive acciones antiguas. |
| N26 | Ajustes, onboarding y diagnóstico | `AegisMenuBar/Views/JarvisWelcomeView.swift`, `JarvisSettingsView.swift`, `JarvisGeneralSettingsView.swift`, `JarvisDiagnosticsSettingsView.swift`, `JarvisHealthView.swift`; `Models/JarvisWelcomeState.swift`, `JarvisSettingsSnapshot.swift`; `Services/JarvisSettingsStore.swift` | Implementado; dueño completa permisos y credenciales | Primer arranque repetible, estado de integraciones/voz/seguridad verdadero, refresco/cancelación de operaciones, borrados solo tras recibo válido, secciones/navegación accesibles y mensajes en español. |
| N27 | Conexión de inferencia Codex con ChatGPT | `AegisMenuBar/Models/JarvisCodexSettings.swift`; `Services/JarvisCodexSettingsStore.swift`; `Views/JarvisCodexSettingsView.swift` | Implementado; acceso oficial completado y conexión/chat/visión comprobados de forma acotada | Estado saneado, catálogo real, selección explícita, prueba sintética y cancelación del acceso; abrir URL oficial validada. No importar `auth.json` ni historial de otra app; no cambio de proveedor o modelo silencioso. |
| N28 | Cuentas, MCP, extensiones y complementos | `AegisMenuBar/Views/JarvisAccountsSettingsView.swift`, `JarvisAccountsGoogleView.swift`, `JarvisExtensionsSettingsView.swift`, `JarvisExtensionReviewView.swift`, `JarvisForeignPluginsView.swift`, `JarvisBrowserSetupView.swift`; `AegisAudioCore/ExtensionsSettings.swift` | Implementado; servicios/configuración y aprobación propios | Habilitar/deshabilitar conexión, flujo OAuth por dueño, vista previa de importación y revisión de herramientas/skills/plugins, gestión de dependencias y cancelación. Los textos de una extensión son datos no fiables; instalar no concede permiso de ejecutar todas sus acciones. |
| N29 | Telegram y dispositivos | `AegisMenuBar/Views/JarvisTelegramSettingsView.swift`, `JarvisDevicesSettingsView.swift`; `Services/JarvisTelegramStore.swift`; `AegisAudioCore/IOSSystemBridge.swift`; `JarvisIOSBridge/main.swift` | Implementado por superficies específicas; bot/dispositivos/permisos requieren configuración | Bot y vinculación de cuenta; Home Assistant mediante configuración; puente Focus/AppIntents y presencia biométrica con desafío. La existencia de `jarvis-ios-bridge` no acredita una app iPhone completa ni soporte universal Android/TV; el catálogo operativo está en el backend. |
| N30 | Spotlight y embeddings locales | `AegisAudioCore/SpotlightIndexer.swift`; `JarvisSpotlightIndexer/main.swift`, `JarvisLocalEmbedding/main.swift` | Implementado; indexación según configuración | NaturalLanguage para embeddings locales con identidad propia; auxiliar acotado y sin red; degradación documentada a FTS cuando no está disponible. Spotlight `upsert/delete/reset_domain` opera sobre IDs ligados a Jarvis, sin ampliar la lectura del sistema. |
| N31 | Distribución, arranque al iniciar sesión y actualizaciones | `AegisMenuBar/App/JarvisLoginItemController.swift`, `JarvisUpdateController.swift`; `AegisAudioCore/JarvisBuildIdentity.swift`, `SecureUpdateLaunchGuard.swift`; `AppBundle/`, `ComputerHelperBundle/`, `packaging/`, `script/` | Implementado local; Developer ID/notarización y actualización publicada pendientes | Una identidad de bundle estable, daemon/auxiliares firmados, versión/revisión/inventario de licencias; `SMAppService` como único registro de login; manifiesto Ed25519 y hashes, prevención de downgrade, cambio atómico y recuperación. Instalador no debe morir a mitad de reemplazo. |
| N32 | Apariencia, localización y accesibilidad | `AegisMenuBar/Views/JarvisAppearance.swift`, `JarvisTheme.swift`, `JarvisInterfaceStyle.swift`, controles compartidos; `Resources/Localizable.xcstrings`, `Resources/es.lproj/Localizable.strings` | Implementado; recorrido VoiceOver P1-12 pendiente | Español como base; modo claro/oscuro y alto contraste; tamaños mínimos; labels de iconos, foco/Tab/Esc/atajos y menús del sistema. La presencia de etiquetas no sustituye una pasada real con VoiceOver. |

## Productos y auxiliares realmente declarados

`native/AegisAudio/Package.swift` es la autoridad del paquete actual: Swift tools 6.2,
macOS 14 como plataforma mínima declarada, `AegisAudioCore` compartido y ONNX Runtime
fijado a 1.24.2 en `AegisVADRuntime`. La compatibilidad efectiva debe verificarse con
el SDK, firma y sistema de destino de la reconstrucción.

| Producto | Target | Papel |
| --- | --- | --- |
| `AegisAudioCore` | `AegisAudioCore` | Contratos, DSP, IPC, seguridad y tipos nativos compartidos. |
| `Jarvis` | `AegisMenuBar` | Aplicación SwiftUI de barra de menús. |
| `aegis-audio-helper` | `AegisAudioHelper` | Comandos de estado/medición/transcripción y envío por IPC. |
| `jarvis-vad-helper` | `JarvisVADHelper` | ONNX/VAD fuera del ejecutable UI. |
| `jarvis-wake-word-trainer` | `JarvisWakeWordTrainer` | Entrenamiento local de activación. |
| `jarvis-speaker-trainer` | `JarvisSpeakerTrainer` | Entrenamiento de identidad del hablante. |
| `jarvis-computer-helper` | `JarvisComputerHelper` | Observación y acciones de UI vinculadas al objetivo. |
| `jarvis-ui-qualification-fixture` | `JarvisUIQualificationFixture` | Ventana sintética de cualificación; no es capacidad de producción. |
| `jarvis-local-embedding` | `JarvisLocalEmbedding` | Vectores locales Apple para memoria. |
| `jarvis-spotlight-indexer` | `JarvisSpotlightIndexer` | Indexación y eliminación del dominio Jarvis. |
| `jarvis-ios-bridge` | `JarvisIOSBridge` | Puente de presencia/biometría y contratos móviles; no app iOS autónoma. |
| `jarvis-biometric-calibrator` | `JarvisBiometricCalibrator` | Calibración de presencia con decisión humana. |

Los directorios `JarvisLocalBrain`, `JarvisMLXEngine` y `JarvisMLXVLM` conservan
fuentes históricas pero no aparecen en la lista de productos/targets activos de ese
manifiesto. No deben habilitarse por su mera presencia. Del mismo modo,
`JarvisModelsSettingsView.swift` y `JarvisAddModelView.swift` aún describen NVIDIA,
mientras la sección activa `.models` de `JarvisSettingsView.swift` abre
`JarvisCodexSettingsView`. El README nativo también conserva instrucciones históricas
de NVIDIA/audio y no se usa aquí como autoridad del proveedor actual.

## Frontera de seguridad y contratos obligatorios

1. **Autoridad humana:** el dueño escribe contraseñas, concede TCC y aprueba acciones.
   Captura, TCC concedido, enrolamiento de voz o login del proveedor no equivalen a
   aprobar una acción. Ningún agente, extensión o runtime alternativo elude el broker.
2. **IPC local:** `AegisAudioCore/LocalIPC.swift` mantiene UDS privado, identidad del
   peer, HMAC/nonce/tiempo y respuesta ligada a solicitud; `ApprovalSigner.swift`
   añade la firma del método y alcance de aprobación. Las nuevas rutas deben disponer
   de contratos explícitos en ambos extremos; no convertir un endpoint HTTP nuevo en
   acceso no firmado al efector nativo.
3. **Plazos:** AGENTS fija un techo de 30 s para llamadas nativas; algunas ramas del
   cliente actual contienen presupuestos específicos más amplios. Es deuda para
   revisar por método, no permiso para aumentar tiempos sin contrato y cancelación.
   `ComputerControlService` limita el proceso efector a un rango de 1–22 s y su salida
   a 65.536 bytes. Los procesos largos de actualización tienen ciclo separado.
4. **Sesión y cancelación:** `UserSessionExecutionGate` y sus permisos/leases impiden
   que una sesión bloqueada o reemplazada continúe actuando. Detener un trabajo debe
   detener sus efectos y esperar limpieza antes de reutilizar el mismo recurso.
5. **Control del Mac:** proceso/ventana/geométrica/fingerprint y cuenta HID vinculados
   a observación; apps/campos sensibles bloqueados; eventos dirigidos al proceso,
   fuera del stream HID global. Revalidación antes y después. Cualquier cambio de
   puntero/teclado físico puede producir `user_takeover`; no se suprime para aprobar
   una demostración. `steps=0` por sí solo no demuestra que no se escribió un prefijo.
6. **Captura mínima:** ventana autorizada, sin captura de audio/cursor; límites en
   geometría, árbol AX e imagen. Observación para actuar, visión activa y adjunto de
   pantalla son contratos distintos; ninguno concede permiso global de leer historial.
7. **Datos privados:** los logs/evaluaciones contienen enums, recuentos y tiempos,
   nunca imágenes, PCM, credenciales o contenido personal. El almacenamiento real del
   dueño sigue en Application Support/Aegis y se migra solo con su contrato explícito.
8. **Inferencia:** Codex con ChatGPT es la primera conexión elegida; NVIDIA no debe
   recibir inferencia, memoria ni audio. Ollama/OpenAI API/Anthropic siguen como
   conexiones posteriores explícitas. Las fuentes MLX y flags heredados no autorizan
   respaldos automáticos ni cambio de modelo.
9. **Firma y recursos:** preservar `ai.aegis.menubar`, namespaces/Keychain `aegis`,
   notices del modelo y recursos propios. La app UI mantiene el presupuesto de 25 MiB
   tras strip, separando VAD/daemon/auxiliares. Versionado no puede depender de la ruta
   del checkout ni de un intérprete instalado en el equipo del dueño.

## Permisos declarados y acciones reservadas al dueño

El manifiesto `native/AegisAudio/AppBundle/Info.plist` declara explicaciones en
español para micrófono, reconocimiento de voz, cámara, Apple Events, calendarios,
contactos y red local. `Jarvis.entitlements` declara automatización Apple Events,
micrófono/audio input, cámara, addressbook y calendars. Son declaraciones: no demuestran
que el permiso se haya concedido ni autorizan probar datos personales.

| Decisión o configuración | Quién la realiza | Prueba permitida después |
| --- | --- | --- |
| Micrófono, Speech, cámara, Screen Recording, Accessibility y otros TCC | Dueño en el aviso de macOS o Ajustes | Estado, denegación/revocación y flujo sintético limitado. |
| Inicio de sesión oficial ChatGPT, OAuth, claves de servicios y llavero de firma | Dueño en la superficie oficial correspondiente | Estado saneado y prueba sintética sin mostrar claves/cuenta. |
| Aprobar una escritura o permiso por app/conversación/siempre | Dueño en tarjeta vigente | Ejecución solo del objetivo ligado; comprobar documento de prueba y recibo. |
| Revocar un permiso | Dueño desde Ajustes | La siguiente acción vuelve a pedir aprobación y no actúa antes de ella. |
| Enrolamiento de palabra/identidad de voz | Dueño aporta voz y decisiones | Medidas y estado sin publicar muestras ni reutilizar TTS como prueba humana. |
| Developer ID, notarización, publicación y aceptación de actualización | Dueño aporta identidad/credenciales y decisión | Gates de distribución, firma/notarización y recuperación verificables. |
| Limpieza de historial, memorias, proyectos y datos de prueba | Dueño mediante la app | Recibos y metadatos de auditoría; ningún borrado por una inferencia de conveniencia. |

## Matriz mínima de recorridos reales para recuperar la paridad

Estos son criterios para la nueva variante; esta revisión de inventario no los ejecutó.

| ID | Recorrido | Resultado comprobable | Evidencia actual / pendiente |
| --- | --- | --- | --- |
| E01 | Instalar, arrancar, cerrar ventanas, reabrir, reiniciar y salir | Un daemon y una identidad; firma/revisión correctas; apagado completo y sin captura involuntaria | Hay gates y pruebas de supervisor/ventanas; repetir en nueva arquitectura. |
| E02 | Chat normal y temporal con envío/cancelación/reintento | Respuestas y borradores en su chat; temporal sin memoria/guardado; siguiente turno funciona tras cancelar | Conexión/chat/visión documentados de forma acotada en `CODEX_CONNECTION_20261023.md`; las regresiones de cancelación no sustituyen repetir el recorrido bajo el nuevo núcleo. |
| E03 | Editar/regenerar con adjunto, valorar, archivar, buscar, papelera/restaurar | Original conservado; identidades válidas; no texto/undo de otro chat; recibos y paginación completos | Cobertura de UI/contratos; estados vacíos/buscador/auditoría de borrados siguen pendientes. |
| E04 | Crear proyecto, recorrer todas sus páginas y cambiar contexto con borrador | Sin pérdida/mezcla de adjuntos o conversaciones; recuperación ante respuesta tardía | Pruebas de paginación/selección/recuperación; repetir con fixture real. |
| E05 | Página web sintética en dos pestañas, capturar seleccionada, cambiar/cerrar en curso | Adjunto corresponde a documento/URL vigente; no publica captura de pestaña anterior | Hay tests BrowserTabs/PageCapture/Keyboard; no equivale a validar todos los sitios/autenticaciones. |
| E06 | Herramienta MCP de lectura y una de escritura aprobada | Catálogo y argumentos correctos, autorización única, efecto y resultado independientes del texto del modelo | Requiere conexión de prueba; la UI de integración por sí sola no valida ejecución. |
| E07 | TextEdit vacío: literal, «Siempre», otro chat, revocación | Texto exacto, recibo firmado, reuso solo permitido, tras revocar nueva aprobación sin efecto | Recorrido real documentado con `36bcc60`. No acredita todas las apps ni la futura reconstrucción. |
| E08 | TextEdit con contenido previo: añadir línea, selección/caret, cancelar a mitad | Contenido anterior íntegro, posición y salto correctos, efecto parcial identificado tras cancelar | **Pendiente específico:** el contrato actual limita saltos de línea y la condición contiene-texto no demuestra posición/preservación. |
| E09 | Captura/acción con app diferente, campo sensible, TCC revocado y usuario moviendo puntero | Denegación/control detenido; objetivo original intacto; sin sustitución silenciosa de app | Amplia cobertura de contratos/helpers; repetir con fixture de UI autorizado. |
| E10 | Voz española: PTT → transcripción → razonamiento → TTS → interrupción | Texto correcto, voz reproducida completa, interrupción inmediata y siguiente turno limpio | **Salida productiva sin configurar**. No certificar con self-test VAD ni login ChatGPT. |
| E11 | Activación/identidad, auriculares/altavoces, suspensión y recuperación | Sin autoactivación por TTS, autoaprobación, mezcla de micrófonos ni reanudación indebida | Entrenamiento/capacidad y pruebas presentes; requiere voz y decisiones reales del dueño. |
| E12 | Reunión sintética iniciar/parar → adjunto → resumen | Captura explícita, sin persistencia de PCM, pausa correcta de otros consumidores de micrófono | Implementación y pruebas de transcripción; nueva prueba real controlada necesaria. |
| E13 | Tarea programada con aprobación, notificación, rechazo y entrega tras reinicio | Inicio verdadero, no ejecución antes del dueño, tarjeta vigente y resultado vinculado al trabajo | Flujos y recibos implementados; volver a verificar bajo el nuevo planificador. |
| E14 | Memoria sintética buscar/olvidar y Spotlight retirar | Borrado confirmado, sin referencia recuperable en búsqueda correspondiente, sin lectura de otros datos | Contratos UI/auxiliares; semántica/cifrado/migración se inventarían también en backend. |
| E15 | Telegram/dispositivo/MCP externo elegido | Solo cuenta/dispositivo autorizado; errores claros y denegación sin acciones laterales | Requiere configuración y aprobaciones del dueño; no está certificado por existir el puente iOS. |
| E16 | Actualización publicada, hash/firma inválidos, aplicación ocupada y corte durante reemplazo | Versión vigente preservada o nueva instalación completa; sin downgrade ni proceso huérfano | Implementación y gates presentes; Developer ID/notarización y actualización publicada pendientes. |
| E17 | VoiceOver, teclado, claro/oscuro, contraste y tamaño mínimo | Todo control alcanzable/nombrado, foco coherente y avisos visibles | Pasadas visuales y etiquetas existentes; **VoiceOver real P1-12 sigue pendiente**. |

## Pendientes y límites que la reconstrucción no debe ocultar

- P0-1 y P0-2: Developer ID/notarización y actualización publicada entre versiones.
- P1-12: recorrido real con VoiceOver y teclado.
- R2-4/R2-5: estado de archivados y unificación del buscador lateral.
- R2-9: auditoría mínima de borrados de chats/proyectos, sin títulos ni contenido.
- R2-16: detalles de decisiones de tareas con etiquetas/fechas en español.
- Audio productivo: conectar proveedor explícito y volver a certificar toda la cadena.
- Control del Mac: añadir línea al final con preservación y posición verificadas.
- Negativas intermitentes al seleccionar herramientas: causa no confirmada en la evidencia
  actual; una interfaz nueva no resuelve por sí sola ese comportamiento.
- Integraciones/dispositivos y modelos de voz: capacidades condicionadas a configuración;
  no se presentan como demostradas en vivo por estar incluidas en la app.

## Localizadores de pruebas y empaquetado

Las pruebas están en `native/AegisAudio/Tests/AegisAudioCoreTests` y
`native/AegisAudio/Tests/AegisMenuBarTests`. Los nombres por área incluyen IPC,
firmas, aprobaciones/recibos, quiet event boundaries, sesión, captura/AX, audio/VAD,
conversación de voz, browser tabs/captures/keyboard, drafts/history/proyectos,
ventanas, ajustes Codex/readiness, accesibilidad, localización y actualizaciones.

Los scripts que hacen concreto el contrato de distribución son
`script/test_native.sh`, `script/build_and_run.sh`, `script/build_daemon_bundle.sh`,
`script/menu_bar_service.sh`, `script/jarvis_beta.sh`, `script/rc3_rc8_gate.sh`,
`script/macos_qualification_gate.sh`, `script/p8_application_gate.sh`,
`script/p9_browser_gate.sh`, `script/p10_voice_gate.sh`,
`script/p12_distribution_release_gate.sh`, `script/p13_self_contained_runtime_gate.sh`,
`script/p14_secure_update_gate.sh`, `script/release_macos.sh`,
`script/sync_localizations.py` y `script/bundle_notices.py`.

El inventario fuente siguiente es una cobertura de archivos, no una afirmación de
paridad funcional ni de ejecución de esas pruebas en esta revisión.


## Apéndice A: manifiesto de fuentes nativas
Se registran **226 archivos Swift de Sources** y **93 archivos Swift de Tests**, obtenidos del índice Git en la base indicada. Los nombres duplicados de iCloud `* 2.*` se excluyen.

### Fuentes por directorio

`native/AegisAudio/Sources/AegisAudioCore`

- `AcousticSensor.swift`
- `ActiveVisionSensor.swift`
- `ApprovalSigner.swift`
- `AssistantPanelLayout.swift`
- `AssistantPresentation.swift`
- `AssistantRuntimeState.swift`
- `AudioEnrollment.swift`
- `AudioMeter.swift`
- `BundledDaemonSupervisor.swift`
- `ChatAttachment.swift`
- `ChatHistory.swift`
- `ChatResponseDocument.swift`
- `ChatSearch.swift`
- `ComputerControl.swift`
- `ConfirmationRecognizer.swift`
- `DualChannelAuthorizer.swift`
- `EngineeringDialogueResponse.swift`
- `ExtensionsSettings.swift`
- `FollowUpListeningGate.swift`
- `GuidedGeneration.swift`
- `IOSSystemBridge.swift`
- `InteractionContracts.swift`
- `JarvisBuildIdentity.swift`
- `JarvisOperationalEvidence.swift`
- `JarvisReadiness.swift`
- `JarvisTerminalCommand.swift`
- `JobMonitoringDeadline.swift`
- `LocalIPC.swift`
- `LocalImage.swift`
- `LocalInferenceConversation.swift`
- `LocalInferenceHelper.swift`
- `LocalVoiceAdapter.swift`
- `LocalVoiceApplicationContext.swift`
- `LocalVoiceCapabilities.swift`
- `LocalVoiceCommandText.swift`
- `LocalVoiceConversationSession.swift`
- `LocalVoiceReplay.swift`
- `LocalVoiceSpeakerIdentity.swift`
- `LocalVoiceTimer.swift`
- `LocalVoiceTurnFeedback.swift`
- `MLXLocalEngine.swift`
- `MascotBehaviorState.swift`
- `MascotMotion.swift`
- `MeetingTranscriber.swift`
- `MicrophoneMeter.swift`
- `NotchExpansionMotion.swift`
- `NotchInteractionState.swift`
- `OnDemandVisionCapture.swift`
- `OwnerPresenceLease.swift`
- `PersonNameRecognizer.swift`
- `ProactiveAlertGate.swift`
- `QuietEventDispatcher.swift`
- `RuntimePowerStatus.swift`
- `SecureUpdateLaunchGuard.swift`
- `SilentEvents.swift`
- `SileroVADDetector.swift`
- `SpeakerEnrollment.swift`
- `SpeakerIdentity.swift`
- `SpeakerModelTraining.swift`
- `SpeechArtifact.swift`
- `SpeechAudioReplayBuffer.swift`
- `SpeechInputProcessor.swift`
- `SpeechOutput.swift`
- `SpeechPlaybackOutcome.swift`
- `SpeechPlayoutPolicy.swift`
- `SpeechStreamChunker.swift`
- `SpeechTranscription.swift`
- `SpotlightIndexer.swift`
- `TextChatTranscript.swift`
- `UserSessionExecutionGate.swift`
- `VADProcessClient.swift`
- `VoiceActivity.swift`
- `WakeWordCapability.swift`
- `WakeWordDetector.swift`
- `WakeWordEnrollment.swift`
- `WakeWordTrainingWindow.swift`
- `WindowCapture.swift`

`native/AegisAudio/Sources/AegisAudioHelper`

- `main.swift`

`native/AegisAudio/Sources/AegisMenuBar/App`

- `AegisMenuBarApp.swift`
- `HUDPanelController.swift`
- `JarvisDockController.swift`
- `JarvisLoginItemController.swift`
- `JarvisPointerController.swift`
- `JarvisSettingsCommands.swift`
- `JarvisUpdateController.swift`
- `MascotAutonomyController.swift`
- `NotchApprovalPanelController.swift`
- `NotchExpansionAnimator.swift`
- `NotchHostingView.swift`
- `NotchPanelController.swift`
- `QuickEntryPanelController.swift`
- `RequestedWindowFocus.swift`
- `VoiceHotKeyController.swift`

`native/AegisAudio/Sources/AegisMenuBar/Models`

- `ActionReviewPresentation.swift`
- `ChatRecovery.swift`
- `ChatSubmission.swift`
- `JarvisActionHistory.swift`
- `JarvisBrowserPageCapture.swift`
- `JarvisBrowserStore.swift`
- `JarvisBrowserTabs.swift`
- `JarvisChatStore+Actions.swift`
- `JarvisChatStore+Drafts.swift`
- `JarvisChatStore+History.swift`
- `JarvisChatStore+Search.swift`
- `JarvisChatStore.swift`
- `JarvisChatWorkspace.swift`
- `JarvisCodexSettings.swift`
- `JarvisComposerAvailability.swift`
- `JarvisInteraction.swift`
- `JarvisScheduledExecution.swift`
- `JarvisSettingsDeletionReceipt.swift`
- `JarvisSettingsSnapshot.swift`
- `JarvisSidebarHistory.swift`
- `JarvisWelcomeState.swift`
- `MenuBarModel+ActionHistory.swift`
- `MenuBarModel+Approvals.swift`
- `MenuBarModel+BrowserChoice.swift`
- `MenuBarModel+Chat.swift`
- `MenuBarModel+Computer.swift`
- `MenuBarModel+Enrollment.swift`
- `MenuBarModel+Interactions.swift`
- `MenuBarModel+Jobs.swift`
- `MenuBarModel+Mascot.swift`
- `MenuBarModel+Meetings.swift`
- `MenuBarModel+Permissions.swift`
- `MenuBarModel+Presence.swift`
- `MenuBarModel+Presentation.swift`
- `MenuBarModel+Runtime.swift`
- `MenuBarModel+Scheduled.swift`
- `MenuBarModel+Voice.swift`
- `MenuBarModel+VoiceCapture.swift`
- `MenuBarModel+VoiceOutput.swift`
- `MenuBarModel+VoiceUtilities.swift`
- `MenuBarModel+WakeWord.swift`
- `MenuBarModel+WakeWordEnrollment.swift`
- `MenuBarModel.swift`
- `ProjectConversationStore.swift`
- `SideChatStore.swift`

`native/AegisAudio/Sources/AegisMenuBar/Services`

- `ComputerControlService.swift`
- `ImageFilePicker.swift`
- `JarvisCodexSettingsStore.swift`
- `JarvisSettingsStore.swift`
- `JarvisTelegramStore.swift`
- `JarvisTerminalLauncher.swift`
- `OwnerPresenceAuthenticator.swift`
- `ProactiveEventMonitor.swift`
- `ScheduledNotificationRouter.swift`
- `ScreenCaptureService.swift`
- `ThermalMonitor.swift`

`native/AegisAudio/Sources/AegisMenuBar/Support`

- `NotchPreview.swift`
- `PresentationPreviewController.swift`

`native/AegisAudio/Sources/AegisMenuBar/Views`

- `HUDComponents.swift`
- `HUDPresenceView.swift`
- `HUDView.swift`
- `JarvisAboutSettingsView.swift`
- `JarvisAccountsGoogleView.swift`
- `JarvisAccountsSettingsView.swift`
- `JarvisActionHistoryView.swift`
- `JarvisAddModelView.swift`
- `JarvisAppearance.swift`
- `JarvisApprovalCard.swift`
- `JarvisBrowserPanel.swift`
- `JarvisBrowserSetupView.swift`
- `JarvisBrowserTabStrip.swift`
- `JarvisChatActionsView.swift`
- `JarvisChatAttachmentsView.swift`
- `JarvisChatComposerView.swift`
- `JarvisChatContextView.swift`
- `JarvisChatHistoryView.swift`
- `JarvisChatResponseView.swift`
- `JarvisChatView.swift`
- `JarvisChatWelcomeView.swift`
- `JarvisCodexSettingsView.swift`
- `JarvisCommandPalette.swift`
- `JarvisComposerTextEditor.swift`
- `JarvisControlSurface.swift`
- `JarvisDecisionCard.swift`
- `JarvisDevicesSettingsView.swift`
- `JarvisDiagnosticsSettingsView.swift`
- `JarvisDotView.swift`
- `JarvisExtensionReviewView.swift`
- `JarvisExtensionsSettingsView.swift`
- `JarvisForeignPluginsView.swift`
- `JarvisGeneralSettingsView.swift`
- `JarvisHealthView.swift`
- `JarvisInterfaceStyle.swift`
- `JarvisMenuPicker.swift`
- `JarvisModelsSettingsView.swift`
- `JarvisNotchMascot.swift`
- `JarvisPlanCard.swift`
- `JarvisPrivacySettingsView.swift`
- `JarvisProjectView.swift`
- `JarvisScheduledView.swift`
- `JarvisSegmentedPicker.swift`
- `JarvisSettingsView.swift`
- `JarvisSidebarSectionHeader.swift`
- `JarvisTelegramSettingsView.swift`
- `JarvisTheme.swift`
- `JarvisToggleControlStyle.swift`
- `JarvisVoiceSettingsView.swift`
- `JarvisWelcomeView.swift`
- `MenuBarComponents.swift`
- `MenuBarPermissionsView.swift`
- `MenuBarPreferencesView.swift`
- `MenuBarView.swift`
- `MenuBarVoiceAction.swift`
- `NotchOutline.swift`
- `NotchPresenceView.swift`
- `SideChatPanel.swift`
- `SpeakerEnrollmentView.swift`
- `SwarmRoleVisuals.swift`
- `VoiceEnrollmentComponents.swift`
- `WakeWordEnrollmentView.swift`

`native/AegisAudio/Sources/AegisVADRuntime`

- `SileroVADRuntime.swift`

`native/AegisAudio/Sources/JarvisBiometricCalibrator`

- `main.swift`

`native/AegisAudio/Sources/JarvisComputerHelper`

- `main.swift`

`native/AegisAudio/Sources/JarvisIOSBridge`

- `main.swift`

`native/AegisAudio/Sources/JarvisLocalBrain`

- `main.swift`

`native/AegisAudio/Sources/JarvisLocalEmbedding`

- `main.swift`

`native/AegisAudio/Sources/JarvisMLXEngine`

- `main.swift`

`native/AegisAudio/Sources/JarvisMLXVLM`

- `main.swift`

`native/AegisAudio/Sources/JarvisSpeakerTrainer`

- `main.swift`

`native/AegisAudio/Sources/JarvisSpotlightIndexer`

- `main.swift`

`native/AegisAudio/Sources/JarvisUIQualificationFixture`

- `main.swift`

`native/AegisAudio/Sources/JarvisVADHelper`

- `main.swift`

`native/AegisAudio/Sources/JarvisWakeWordTrainer`

- `main.swift`

### Pruebas por target

`native/AegisAudio/Tests/AegisAudioCoreTests`

- `AcousticSensorTests.swift`
- `ActiveVisionSensorTests.swift`
- `ApprovalSignerTests.swift`
- `AssistantPresentationTests.swift`
- `AudioEnrollmentTests.swift`
- `AudioMeterTests.swift`
- `BundledDaemonSupervisorTests.swift`
- `ChatAttachmentTests.swift`
- `ChatResponseDocumentTests.swift`
- `ComputerAXObservationMetadataTests.swift`
- `ComputerControlTests.swift`
- `ComputerPerceptionSafetyTests.swift`
- `DualChannelAuthorizerTests.swift`
- `ExtensionsSettingsTests.swift`
- `FollowUpListeningGateTests.swift`
- `InteractionContractTests.swift`
- `JarvisBuildIdentityTests.swift`
- `JarvisOperationalEvidenceTests.swift`
- `JarvisReadinessTests.swift`
- `JarvisTerminalDirectoryTests.swift`
- `JobMonitoringDeadlineTests.swift`
- `LocalIPCTests.swift`
- `LocalIPCTransportTests.swift`
- `LocalImageTests.swift`
- `LocalInferenceConversationTests.swift`
- `LocalVoiceApplicationContextTests.swift`
- `LocalVoiceCapabilitiesTests.swift`
- `LocalVoiceConversationSessionTests.swift`
- `LocalVoiceReplayTests.swift`
- `LocalVoiceSpeakerIdentityTests.swift`
- `LocalVoiceTimerTests.swift`
- `LocalVoiceTurnFeedbackTests.swift`
- `MascotBehaviorTests.swift`
- `MascotMotionTests.swift`
- `MeetingTranscriptTests.swift`
- `NotchExpansionMotionTests.swift`
- `NotchInteractionTests.swift`
- `OwnerPresenceLeaseTests.swift`
- `PersonNameRecognizerTests.swift`
- `ProactiveAlertGateTests.swift`
- `QuietEventBoundaryTests.swift`
- `RuntimePowerStatusTests.swift`
- `SecureUpdateLaunchGuardTests.swift`
- `SileroVADTests.swift`
- `SpeakerEnrollmentTests.swift`
- `SpeakerIdentityTests.swift`
- `SpeechArtifactTests.swift`
- `SpeechAudioReplayBufferTests.swift`
- `SpeechOutputTests.swift`
- `SpeechPlayoutPolicyTests.swift`
- `SpeechStreamChunkerTests.swift`
- `SpeechTranscriptionTests.swift`
- `TextChatTranscriptTests.swift`
- `UserSessionExecutionGateTests.swift`
- `VoiceActivityTests.swift`
- `VoiceChatContinuityTests.swift`
- `WakeWordCapabilityTests.swift`
- `WakeWordDetectorTests.swift`
- `WakeWordEnrollmentTests.swift`
- `WakeWordTrainingWindowTests.swift`
- `WindowCaptureTests.swift`

`native/AegisAudio/Tests/AegisMenuBarTests`

- `ActionHistoryTests.swift`
- `AppGrantReceiptTests.swift`
- `BrowserKeyboardTests.swift`
- `BrowserPageCaptureTests.swift`
- `BrowserTabsTests.swift`
- `ChatComposerTests.swift`
- `ChatDraftRetentionTests.swift`
- `ChatMessageActionsTests.swift`
- `ChatSubmissionTests.swift`
- `ChatWorkspaceTests.swift`
- `CodexVoiceReadinessTests.swift`
- `CommandPaletteSelectionTests.swift`
- `ConversationSearchTests.swift`
- `HealthReportTests.swift`
- `JarvisChatStoreTests.swift`
- `JarvisCodexSettingsTests.swift`
- `JarvisDistributionTests.swift`
- `JarvisLocalizationTests.swift`
- `JarvisPlanAndDateTests.swift`
- `JarvisScheduleDraftTests.swift`
- `JarvisVisualAccessibilityTests.swift`
- `JarvisWelcomeBrowserTests.swift`
- `JarvisWelcomeSettingsStateTests.swift`
- `JarvisWindowLifecycleTests.swift`
- `ProjectConversationPaginationTests.swift`
- `ProjectStoreRecoveryTests.swift`
- `ScheduledExecutionReceiptTests.swift`
- `SettingsMutationLifecycleTests.swift`
- `SideChatStoreTests.swift`
- `SidebarAccessibilityTests.swift`
- `SidebarHistoryTests.swift`
- `VoiceApprovalScopeTests.swift`

### Recursos y bundles

`native/AegisAudio/AppBundle`

- `Info.plist`
- `Jarvis.entitlements`

`native/AegisAudio/AppBundle/Resources`

- `Jarvis.icns`
- `JarvisLogo.png`

`native/AegisAudio/AppBundle/es.lproj`

- `InfoPlist.strings`

`native/AegisAudio/ComputerHelperBundle`

- `Info.plist`

`native/AegisAudio/Sources/AegisMenuBar/Resources`

- `JarvisMascotBody.png`
- `JarvisTricksterDot.png`
- `Localizable.xcstrings`

`native/AegisAudio/Sources/AegisMenuBar/Resources/es.lproj`

- `Localizable.strings`

`native/AegisAudio/Sources/AegisVADRuntime/Resources`

- `SILERO_NOTICE.txt`
- `silero-vad.onnx`

## Apéndice B: nombres literales de contratos visibles desde fuentes Swift

Este catálogo se extrajo de cadenas literales con forma de nombre de contrato. No contiene argumentos ni datos del dueño. Incluye candidatos a métodos IPC y etiquetas de eventos/protocolos, como `audio.meter` o `speech.transcript`; estos últimos no se presentan como handlers del daemon. Excluye interpolación y no sustituye el registro autorizado del daemon: los wrappers aceptan métodos dinámicos, y los servidores MCP/plugins amplían su propio catálogo. Las rutas históricas presentes en fuentes tampoco se habilitan por aparecer aquí.

| Familia | Nombres literales observados |
| --- | --- |
| `actions` | `actions.history.delete`, `actions.history.list` |
| `approval` | `approval.register_signer` |
| `attention` | `attention.list`, `attention.snooze` |
| `audio` | `audio.meter`, `audio.session.close`, `audio.session.resume`, `audio.status` |
| `audit` | `audit.system.event` |
| `biometric` | `biometric.training.sample_ready` |
| `browser` | `browser.discover` |
| `computer` | `computer.complete`, `computer.wait` |
| `conversations` | `conversations.create`, `conversations.delete`, `conversations.empty_trash`, `conversations.feedback`, `conversations.file`, `conversations.fork`, `conversations.list`, `conversations.messages`, `conversations.rename`, `conversations.restore`, `conversations.search`, `conversations.trash` |
| `dot` | `dot.overview`, `dot.pause`, `dot.resume` |
| `extensions` | `extensions.plugin.details`, `extensions.plugin.import`, `extensions.plugin.preview`, `extensions.plugin.remove`, `extensions.plugin.set_enabled`, `extensions.plugin.verify`, `extensions.skill.details`, `extensions.skill.import`, `extensions.skill.preview`, `extensions.skill.remote`, `extensions.skill.remove`, `extensions.skill.set_enabled`, `extensions.status` |
| `goals` | `goals.delete`, `goals.list`, `goals.resume`, `goals.stop` |
| `health` | `health.status` |
| `image` | `image.submit` |
| `jobs` | `jobs.app_grants.list`, `jobs.app_grants.revoke`, `jobs.approve`, `jobs.approve.app.always`, `jobs.approve.app.conversation`, `jobs.approve.standing`, `jobs.approve.voice`, `jobs.cancel`, `jobs.grants.list`, `jobs.grants.revoke`, `jobs.status`, `jobs.wait` |
| `memory` | `memory.delete`, `memory.search` |
| `personality` | `personality.get`, `personality.save` |
| `plugins` | `plugins.foreign.install`, `plugins.foreign.marketplace.add`, `plugins.foreign.marketplace.remove`, `plugins.foreign.remove`, `plugins.foreign.status` |
| `privacy` | `privacy.permission.denied` |
| `projects` | `projects.assign`, `projects.conversations`, `projects.delete`, `projects.file.add`, `projects.file.remove`, `projects.list`, `projects.save` |
| `provider` | `provider.status` |
| `runtime` | `runtime.power.status`, `runtime.preflight` |
| `schedules` | `schedules.delete`, `schedules.deliveries`, `schedules.list`, `schedules.run_now`, `schedules.save`, `schedules.set_enabled` |
| `security` | `security.status` |
| `settings` | `settings.browser.save`, `settings.cancel`, `settings.credential.set`, `settings.google.connect`, `settings.google.disconnect`, `settings.google.test`, `settings.inference.cancel`, `settings.inference.connect`, `settings.inference.disconnect`, `settings.inference.save`, `settings.inference.status`, `settings.inference.test`, `settings.mcp.import`, `settings.mcp.remove`, `settings.mcp.set_enabled`, `settings.models.save`, `settings.models.test`, `settings.operation`, `settings.status` |
| `speech` | `speech.context`, `speech.release`, `speech.status`, `speech.stream.cancel`, `speech.stream.close`, `speech.stream.next`, `speech.stream.open`, `speech.synthesize`, `speech.transcript` |
| `swarm` | `swarm.activity`, `swarm.submit`, `swarm.wait` |
| `system` | `system.focus.update` |
| `telegram` | `telegram.configure`, `telegram.conversation.select`, `telegram.journal.reset`, `telegram.pair.approve`, `telegram.pair.begin`, `telegram.set_enabled`, `telegram.status`, `telegram.unlink` |
| `vision` | `vision.active.observe`, `vision.localized.analyze` |
| `voice` | `voice.submit` |

Las aprobaciones por app construyen además métodos a partir del alcance, por ejemplo `jobs.approve.app.conversation` y `jobs.approve.app.always`; sus variantes y firmas deben contrastarse con `ApprovalSigner.swift`, `LocalIPC.swift` y el inventario backend.
