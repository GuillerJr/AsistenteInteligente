# Inventario del backend de Jarvis para la reconstrucción propia

> Nombres normalizados a Jarvis el 24-10-2026 por indicación del dueño. Este
> inventario conserva las capacidades y hashes históricos; las rutas se muestran
> con la nomenclatura actual. El snapshot original se conserva en Git, commit
> `dfd0e7c2b2356b5ce22b252af08263c47a2a0c3b`, sin alterar su contenido histórico.


> Snapshot histórico del producto anterior. El código referido fue retirado del
> árbol de trabajo al iniciar la base limpia. Sus estados describen la revisión
> histórica indicada, no capacidades disponibles en el núcleo nuevo. Consultar
> [CLEAN_START.md](CLEAN_START.md) y el historial Git `c97ed89`.

Fecha de corte: 24-10-2026. Fuente propia: revisión `4122a6e2e4ed5160abf2fc4480e752e6ab779197`.
Este documento describe el sistema que debe recuperar la nueva variante de Jarvis en este
repositorio. No es una propuesta de copiar el código de OpenClaw ni de convertir Jarvis en un fork.
La investigación externa y el diseño del nuevo núcleo se registran por separado.

## Alcance y significado de los estados

Se inventariaron **255 archivos Python** de `src/jarvis_core`, mediante rutas y AST, sin importar
ni ejecutar el daemon, herramientas o conectores. Se excluyeron duplicados iCloud `* 2.*`.
Se leyeron manifiestos de `integrations/` y la lista `docs/V1_PENDIENTES.md`; no se abrió
`auth.json`, historial del dueño ni su almacenamiento de ejecución.

- **Implementado:** hay contrato y código; no significa que una función esté configurada o
  que todos sus recorridos hayan pasado pruebas reales.
- **Condicionado:** necesita permiso TCC, dispositivo, cuenta, peer o revisión del dueño.
- **Pendiente:** existe un límite funcional o una certificación aún abierta.
- **Heredado:** conserva compatibilidad o referencias históricas; no autoriza reactivarlo.

El catálogo fijo se obtiene de 44 sitios `ToolDefinition(...)`: 37 tienen nombre literal,
cuatro resuelven constantes de clase y tres son fábricas. La fábrica de navegador produce tres
nombres conocidos; las otras dos producen catálogos MCP/plugins dependientes de configuración.
El resultado es **44 nombres fijos identificados, más las familias dinámicas** de este documento.
No se consultó la configuración privada para fingir un número de herramientas activas.

## Dominios que debe recuperar la nueva variante

| ID | Capacidad y fuentes propias | Estado a preservar | Criterio de prueba real para la nueva variante |
| --- | --- | --- | --- |
| B01 | Arranque y supervisión: `runtime/daemon.py`, `runtime/lifecycle.py`, `runtime/frozen_entrypoint.py`, `runtime_preflight.py`, `runtime_state.py`, `bundle_paths.py`, `config.py` | Implementado; daemon empaquetado, recuperación y readiness | Arrancar desde la app instalada, terminar/reiniciar el daemon y recuperar conexión sin duplicar trabajos. |
| B02 | Contratos y estado: `contracts.py`, `job_contracts.py`, `job_state.py`, `models.py`, `langgraph_compat.py` | Implementado; esquemas tipados, modalidades y separación de riesgo/capacidad | Rechazar entradas inválidas y mantener semántica de estados entre app y núcleo nuevo. |
| B03 | IPC local: `ipc/`, `approval_signing.py`, `security.py`, `secrets.py` | Implementado; cliente firmado, autenticación, framing, replay y firmas de aprobación | App firmada admitida; cliente ajeno denegado; firma/digest/TTL incorrectos no producen efecto. |
| B04 | Inferencia configurada: `inference_settings.py`, `inference_service.py`, `providers/codex.py`, `brain/inference_client.py`, `brain/nvidia_client.py`, `provider_status.py` | Codex con ChatGPT es la conexión productiva; nombre `nvidia_client.py` heredado no identifica el proveedor activo | Inicio de sesión oficial separado, selección del modelo, respuesta, imagen, cancelación y consulta siguiente; ninguna llamada a NVIDIA. |
| B05 | Planificación y diálogo: `orchestration/`, `brain/`, `request_intent.py`, `dialogue.py`, `dialogue_stability.py`, `conversation_quality.py`, `personality.py`, `owner_language.py`, `style.py` | Implementado con reglas específicas y negativas intermitentes aún no resueltas | Variaciones naturales de una petición conservan destino y alcance; una redacción en el chat no ejecuta servicios externos. |
| B06 | Admisión y ciclo del trabajo: `jobs.py`, `job_admission.py`, `job_lifecycle.py`, `job_graph.py`, `job_tasks.py`, `job_completion.py`, `job_failures.py`, `completion_boundary.py`, `async_tasks.py` | Implementado; cola, estados terminales, parciales, cancelación y limpieza | Cancelar durante planificación/espera/ejecución, abrir otro chat y comprobar que el trabajo nuevo funciona y el anterior no continúa. |
| B07 | Autorización y ejecución: `job_authorization.py`, `job_execution.py`, `job_tool_runtime.py`, `tools/broker.py`, `tools/confirmations.py`, `tools/execution.py`, `tools/silent_executor.py` | Implementado; una autoridad final y validación antes del efecto | La llamada exacta aprobada se ejecuta una vez; cambiar argumentos, rol, herramienta o conversación invalida la autorización. |
| B08 | Permisos por aplicación y acciones permanentes: `computer_app_grants.py`, `standing_grants.py`, `job_interactions.py`, `job_ipc.py` | Implementado; TextEdit «Siempre», otro chat y revocación acreditados de forma acotada en `36bcc60` | Aprobar como dueño, ejecutar en otro chat sin pedir de nuevo, revocar y comprobar que ninguna acción posterior ocurre sin aprobación. |
| B09 | Control visual del Mac: `tools/computer.py`, `computer_postconditions.py`, `computer_relay.py`, `computer_workflow.py`, `native_receipts.py`, `background_automation.py` | Implementado con límites: observaciones, enlace a controles, intervención del usuario, pasos y postcondiciones; nueva línea al final pendiente | Escribir y verificar literal; cambiar foco/pantalla entre observar y actuar debe bloquear; no repetir resultados parciales. Para nueva línea exigir posición y conservación del valor anterior. |
| B10 | Acciones nativas y sistema: `tools/defaults.py`, `automation_errors.py`, `verification.py` | Implementado/condicionado a TCC y apps instaladas | Batería/almacenamiento, audio, reproducción, Spotlight, Mail, Calendario, Recordatorios, Contactos, apertura de apps y Atajos con datos de prueba. |
| B11 | Investigación y lectura web: `tools/web.py`, `tools/defaults.py` | Implementado; error anti-robots se distingue de vacío válido | Buscar un hecho público y abrir la fuente; simular proveedor caído y comprobar error explícito sin afirmar ausencia de resultados. |
| B12 | Navegación por tarea y sesiones: `mcp/browser_tasks.py`, `mcp/browser_session.py`, `browser_preferences.py`, `tools/chrome_cdp.py`, `browser_qualification.py` | Implementado/condicionado; destino aprobado por tarea, snapshot/URL/hash, modos aislado/extensión/Chrome | Abrir fixture aprobada, observar, actuar usando referencia fresca, verificar valor/URL y cerrar acceso; pestañas personales conservadas. |
| B13 | MCP: `mcp/client.py`, `host_manager.py`, `discovery.py`, `review.py`, `setup.py`, `local_peers.py`, `result_text.py` | Implementado/condicionado; admisión positiva, proceso y catálogo revisados, esquemas fijados | Conectar peer revisado y ejecutar lectura sintética; cambio de esquema/herramienta, timeout o permiso ausente debe fallar cerrado. |
| B14 | Integraciones curadas: `mcp/integrations.py`, `collections.py`, `google_workspace.py`, `home_assistant.py` | GitHub, Google Workspace, archivos, Playwright y Home Assistant condicionados a su configuración | Listar elementos sintéticos, resolver «el segundo/todos» dentro de la lista acreditada y verificar lectura/lote; no ampliar cuenta o destino. |
| B15 | Plugins y paquetes de capacidades: `plugins/contracts.py`, `store.py`, `runtime.py`, `service.py`, `context7.py`, `extensions_service.py` | Implementado/condicionado; habilitación, manifiesto, hash de esquema, riesgo, hosts y credenciales | Instalar paquete de prueba revisado, verificar catálogo, habilitar/deshabilitar, denegar herramienta fuera del manifiesto y reiniciar con integridad. |
| B16 | Importación de habilidades externas: `plugins/foreign.py`, `foreign_service.py`, `skills/importer.py` | Implementado; fuentes Codex/Claude se convierten en habilidades bajo política Jarvis | Importar una habilidad sintética autorizada; rechazar comandos de instalación o ampliaciones de autoridad incluidas en contenido externo. |
| B17 | Habilidades y aprendizaje: `skills/`, `capability_learning.py`, `capability_blueprints.py`, `capability_reviews.py`, `capability_review_gate.py` | Implementado; procedimientos enseñados y lagunas requieren revisión, no autoinstalación | Enseñar procedimiento sintético, activarlo sólo en su alcance y registrar una carencia sin inventar herramienta ni instalarla automáticamente. |
| B18 | Persistencia de memoria: `memory/crypto.py`, `sqlite.py`, `storage.py`, `migrations.py`, `schema.py`, `codec.py`, `visibility.py`, `capacity.py` | Implementado; cifrado autenticado, namespaces, límites, recuperación y escritura privada | Guardar dato sintético, reiniciar y recuperar; clave/tag alterados bloquean; una migración conserva contenido y no escribe texto privado en claro. |
| B19 | Recuperación y perfil: `memory/retrieval.py`, `retrieval_planning.py`, `search_repository.py`, `vector_db.py`, `embedding.py`, `profile.py`, `semantic_profile.py`, `social.py`, `providers/apple_embedding.py` | Implementado; embeddings Apple, búsqueda semántica/híbrida y perfil; calidad requiere escenarios propios | Recordar dato relevante tras reinicio, no mezclar namespaces ni identidades de índice, borrar y confirmar que ya no se recupera. |
| B20 | Grafo de memoria: `memory/graph_*.py`, `shared_context_ranking.py`, `shared_conversation_context.py`, `spotlight_sync.py` | Implementado; extracción, relaciones y contexto compartido bajo límites | Relaciones sintéticas con procedencia; respetar borrado y visibilidad; memoria equivocada o de otro chat no altera una instrucción explícita. |
| B21 | Conversaciones e historial: `memory/conversation_*.py`, `conversations.py`, `records.py`, `memory/service.py`, `chat_attachments.py` | Implementado; cifrado, temporal, búsqueda, paginación, archivo/papelera, bifurcación, adjuntos y feedback | Chat temporal no persiste ni consulta memoria; buscar/editar/regenerar/restaurar con fixtures; archivo distinto de borrado. |
| B22 | Checkpoints: `memory/langgraph_checkpoint.py`, `task_checkpoint.py`, `conversation_context.py`, `conversation_evidence.py` | Implementado; recuperación del estado y evidencia vinculada | Reiniciar a mitad de tarea recuperable y conservar estado sin repetir un efecto ya realizado o tratado como incierto. |
| B23 | Proyectos: `projects.py` | Implementado; instrucciones, conocimiento, archivos y asignación de chats | Crear proyecto sintético, adjuntar archivo autorizado, aislar contexto y recuperar tras reinicio/corrupción controlada. |
| B24 | Tareas, objetivos y trabajo proactivo: `scheduling.py`, `goals.py`, `dot.py`, `attention.py`, `activity.py`, `mascot.py` | Implementado; presupuestos, pausas, entregas recuperables, pendientes y avisos | Una ejecución programada real, pausa y reanudación; si necesita aprobación queda pendiente y notifica sin concederla. |
| B25 | Telegram: `telegram/` | Implementado/condicionado; bot y dueño enlazados, mensajes, inbox, journal y aprobación de dos pasos | Bot de prueba del dueño, reconexión sin duplicar backlog, respuesta y revisión; otro usuario/chat no puede autorizar. Audio está sin configurar. |
| B26 | Televisores: `tools/smart_tv.py`, `tools/device_config.py` | Implementado/condicionado; SSDP acotado y control de dispositivos preconfigurados | Descubrimiento sin encendido; TCC denegado produce error controlado; cambios sólo al dispositivo exacto aprobado. |
| B27 | Android: `tools/android_automation.py` | Implementado/condicionado; ADB inalámbrico y acciones acotadas | Dispositivo de prueba configurado, lectura UI y acción autorizada; denegar dispositivo, comando o parámetros no permitidos. |
| B28 | iPhone/iPad por atajos: `tools/ios_bridge.py` | Implementado/condicionado; identificadores opacos, foco y comprobaciones críticas | Ejecutar un atajo de prueba permitido; operación crítica exige la comprobación del dueño y no acepta parámetros libres. App iPhone propia pendiente. |
| B29 | Audio, voz y biometría: `audio/`, `speech.py`, `speech_pcm.py`, `biometric_training_service.py`, `voice_qualification.py`, `tcc_privacy.py` | Infraestructura y contratos presentes; TTS productivo `None` y voz/notas de audio sin configurar tras retirar NVIDIA | Transcribir español, generar voz con proveedor elegido e interrumpir sin eco; pruebas físicas del dueño para hablante/ruido. Voz no autoriza mutaciones. |
| B30 | Auditoría, historial de acciones y feedback: `tools/audit.py`, `audit_anchor.py`, `action_history.py`, `action_history_ipc.py`, `evaluation.py`, `job_metrics.py`, `feedback.py`, `job_tool_presenter.py` | Implementado; integridad, metadatos privados mínimos, errores y verificación separados | Registro coherente con efecto real y reinicio; historial de acciones no incluye chats temporales ni contenido; su borrado no deshace acciones. |
| B31 | Ingeniería y CLI: `engineering.py`, `engineering_cli.py`, `engineering_input.py`, `engineering_terminal.py`, `cli.py`, `cli_entrypoint.py` | Implementado; consulta técnica, stdin, PTY, reanudación/cancelación y administración; repositorio autorizado de lectura, sin shell libre | Sesión interactiva y por pipe en fixture, cancelación, lectura fuera de raíz denegada y salida íntegra; cliente CLI no suplanta firma del dueño. |
| B32 | Distribución, actualizaciones y diagnóstico: `secure_update*.py`, `distribution_archive.py`, `build_info.py`, `release_scope.py`, `health.py`, `performance_profiler.py`, `runtime_preflight.py` | Implementado parcialmente certificado; Developer ID/notarización y salto de versión públicos pendientes del dueño | Firma, tamaño, instalación limpia, manifiesto/Ed25519/hash/revisión, recuperación de actualización interrumpida y rechazo de downgrade. |
| B33 | Calificación: `*_qualification.py`, `acceptance_benchmark.py`, `production_workflows.py`, `long_horizon_reliability.py`, `dialogue_challenges.py`, `skill_flow_benchmark.py`, `skill_flow_live.py`, `failure_boundary.py` | Implementado; pruebas deterministas y muestras reales tienen autoridades distintas | Mantener la batería determinista y añadir evidencia real del recorrido completo por módulo; no declarar toda la app certificada por un puntaje sintético. |

Las rutas cortas de esta tabla son relativas a `src/jarvis_core/`. La lista exacta de los 255
archivos se conserva al final para que una función transversal no desaparezca por no estar en
una tabla resumida. `Bxx` es una identidad de inventario, no un estado de implementación nueva.

## Herramientas con nombre fijo

La columna indica el campo `requires_confirmation` declarado, no el resultado final de política.
El broker puede exigir o denegar por rol, riesgo, firma, modo plan, intención local exacta y
contexto. Las acciones `browser_task_action` requieren el acceso aprobado y enlazado a su tarea.
Una observación o una propuesta del modelo no es permiso ni comprobación de éxito.

| Nombre técnico que debe mapearse | Fuente | Campo de confirmación |
| --- | --- | --- |
| `android_device_control` | `src/jarvis_core/tools/android_automation.py:368` | sí |
| `application_open` | `src/jarvis_core/tools/defaults.py:710` | sí |
| `browser_open_url` | `src/jarvis_core/tools/defaults.py:685` | sí |
| `browser_play_media` | `src/jarvis_core/tools/defaults.py:698` | sí |
| `browser_search` | `src/jarvis_core/tools/defaults.py:671` | sí |
| `browser_task_action` | `src/jarvis_core/mcp/browser_tasks.py:428` | no; exige sesión aprobada |
| `browser_task_close` | `src/jarvis_core/mcp/browser_tasks.py:428` | no; solo desconexión de la tarea |
| `browser_task_open` | `src/jarvis_core/mcp/browser_tasks.py:428` | sí |
| `calendar_create_event` | `src/jarvis_core/tools/defaults.py:613` | sí |
| `calendar_list_events` | `src/jarvis_core/tools/defaults.py:603` | no (valor predeterminado) |
| `computer_use` | `src/jarvis_core/tools/defaults.py:735` | sí |
| `contact_create` | `src/jarvis_core/tools/defaults.py:662` | sí |
| `contacts_search` | `src/jarvis_core/tools/defaults.py:651` | no (valor predeterminado) |
| `filesystem_read_text` | `src/jarvis_core/tools/defaults.py:551` | no (valor predeterminado) |
| `ios_shortcut_run` | `src/jarvis_core/tools/ios_bridge.py:318` | sí |
| `jarvis_goal_create` | `src/jarvis_core/goals.py:236` | sí |
| `jarvis_goal_list` | `src/jarvis_core/goals.py:244` | no (valor predeterminado) |
| `jarvis_goal_stop` | `src/jarvis_core/goals.py:249` | sí |
| `jarvis_plugin_install` | `src/jarvis_core/plugins/foreign_service.py:74` | sí |
| `jarvis_plugin_search` | `src/jarvis_core/plugins/foreign_service.py:67` | no (valor predeterminado) |
| `jarvis_schedule_create` | `src/jarvis_core/scheduling.py:388` | sí |
| `jarvis_schedule_delete` | `src/jarvis_core/scheduling.py:407` | sí |
| `jarvis_schedule_list` | `src/jarvis_core/scheduling.py:399` | no (valor predeterminado) |
| `jarvis_skill_create` | `src/jarvis_core/skills/teach.py:58` | sí |
| `mail_list_recent` | `src/jarvis_core/tools/defaults.py:583` | no (valor predeterminado) |
| `mail_send_message` | `src/jarvis_core/tools/defaults.py:594` | sí |
| `media_control` | `src/jarvis_core/tools/defaults.py:518` | sí |
| `network_discover_hosts` | `src/jarvis_core/tools/defaults.py:752` | sí |
| `reminder_complete` | `src/jarvis_core/tools/defaults.py:642` | sí |
| `reminder_create` | `src/jarvis_core/tools/defaults.py:633` | sí |
| `reminders_list` | `src/jarvis_core/tools/defaults.py:622` | no (valor predeterminado) |
| `shortcut_run` | `src/jarvis_core/tools/defaults.py:723` | sí |
| `smart_tv_control` | `src/jarvis_core/tools/smart_tv.py:675` | sí |
| `smart_tv_discover` | `src/jarvis_core/tools/smart_tv.py:690` | no (valor predeterminado) |
| `spotlight_open` | `src/jarvis_core/tools/defaults.py:542` | sí |
| `spotlight_search` | `src/jarvis_core/tools/defaults.py:531` | no (valor predeterminado) |
| `system_audio_set` | `src/jarvis_core/tools/defaults.py:505` | sí |
| `system_describe_runtime` | `src/jarvis_core/tools/defaults.py:468` | no (valor predeterminado) |
| `system_observe_status` | `src/jarvis_core/tools/defaults.py:494` | no (valor predeterminado) |
| `system_power_status` | `src/jarvis_core/tools/defaults.py:478` | no (valor predeterminado) |
| `system_storage_status` | `src/jarvis_core/tools/defaults.py:486` | no (valor predeterminado) |
| `terminal_run_template` | `src/jarvis_core/tools/defaults.py:765` | sí |
| `web_fetch` | `src/jarvis_core/tools/defaults.py:573` | no (valor predeterminado) |
| `web_research` | `src/jarvis_core/tools/defaults.py:560` | no (valor predeterminado) |

## Familias dinámicas y herramientas remotas curadas

| Familia | Fuente y expansión | Estado y restricciones |
| --- | --- | --- |
| MCP configurado | `mcp/client.py:1624`; nombres `mcp_<servidor>__<herramienta_normalizada>`, con digest/sufijo ante longitud o colisión | Catálogo y esquema revisados; cada herramienta MCP expuesta declara confirmación. El conjunto activo depende del dueño y no se inventaría leyendo sus secretos. |
| Plugins de capacidad | `plugins/runtime.py:422`; `plugin_<plugin>__<conector>__<herramienta>` | Manifiesto, riesgo, roles, capacidades y hosts. Confirmación declarada para HIGH/CRITICAL, además de la política global. |
| Navegador por tarea | `mcp/browser_tasks.py:428`; tres nombres fijos incluidos arriba | Consentimiento inicial ligado al objetivo y origen; literales, snapshot/hash/URL y TTL acotados para cada acción. |
| Context7 | `plugins/context7.py`; `resolve-library-id`, `query-docs` | Paquete opcional `context7-docs`, con esquema revisado; rechaza secretos, rutas personales, correos y código privado. No confundir tener el paquete en fuentes con tenerlo activo. |
| Habilidades importadas | `skills/importer.py`, `plugins/foreign.py` | Instrucciones y selección de herramientas ya autorizadas; no crean por sí solas ejecutores o permisos nuevos. |

`mcp/integrations.py` admite las siguientes herramientas remotas por nombre. Son **32 reglas
curadas**; el servidor puede tener más herramientas, pero eso no las autoriza en Jarvis.

- **GitHub (4):** `get_file_contents`, `list_commits`, `issue_read`, `pull_request_read`.
- **Google Workspace (13):** `search_gmail_messages`, `get_gmail_message_content`,
  `get_gmail_messages_content_batch`, `draft_gmail_message`, `list_calendars`, `get_events`,
  `manage_event`, `search_drive_files`, `get_drive_file_content`, `list_drive_items`,
  `create_drive_folder`, `create_drive_file`, `update_drive_file`.
- **Archivos (5):** `read_text_file`, `read_multiple_files`, `list_directory`, `get_file_info`,
  `list_allowed_directories`.
- **Playwright (9):** `browser_navigate`, `browser_snapshot`, `browser_close`, `browser_click`,
  `browser_type`, `browser_select_option`, `browser_press_key`, `browser_tabs`, `browser_wait_for`.
- **Home Assistant (1):** `GetLiveContext`. Los controles de entidades no están admitidos por
  esa regla de solo lectura; necesitan diseño/revisión explícitos.

`mcp/collections.py` añade el lote **local derivado** `get_drive_file_content_many` (máximo
cinco archivos), que llama a la lectura revisada por identificador bajo una aprobación.
Debe conservarse la relación lista → identificador → detalle/lote para referencias como
«el segundo», «todos» o «el de …», junto con cuenta, contenedor y alcance originales.

Los manifiestos propios fijan `@modelcontextprotocol/server-filesystem@2026.8.31` y
`@playwright/mcp@0.0.80` en `integrations/mcp/package.json`, y `workspace-mcp==1.26.1`
en `integrations/google-workspace/pyproject.toml`. Una reconstrucción puede reemplazar los
peers, pero necesita contratos y revisión de versión; no debe introducir descargas automáticas
mediante `npx` ni actualizar esquemas sin aprobación.

## Superficies que no son ToolDefinition

La nueva arquitectura debe mapear también los servicios IPC. No basta con copiar la tabla de
herramientas ofrecidas al modelo.

- **Trabajo y aprobación:** `swarm.submit`, `voice.submit`, `image.submit`, `jobs.status`,
  `jobs.wait`, `jobs.cancel`, `jobs.approve`, `jobs.approve.voice`, `jobs.approve.standing`,
  `jobs.approve.app.conversation`, `jobs.approve.app.always`, `jobs.app_grants.list/revoke`,
  `jobs.grants.list/revoke`, `jobs.metrics`, `approval.register_signer` (`job_ipc.py`).
- **Inferencia:** estado, conexión, cancelación, desconexión, guardado y prueba bajo
  `settings.inference.*` (`inference_service.py`). `settings.models.*` y comandos NVIDIA
  antiguos no son permiso para reactivar el proveedor retirado.
- **Datos del producto:** `memory.*`, `conversations.*`, `projects.*`, `personality.*`,
  `schedules.*`, `goals.*`, `attention.*`, `dot.*`, `mascot.plan.*`, incluyendo búsqueda,
  bifurcación, archivo/papelera, feedback, conocimiento del proyecto y entregas.
- **Configuración e integraciones:** `settings.*`, operaciones/cancelaciones, cuenta Google,
  navegador, importación de MCP, `extensions.*`, `plugins.foreign.*`.
- **Audio y sistema:** sesiones/medidores/eventos/transcripción, estado de energía,
  entrenamiento biométrico, síntesis y artefactos PCM, con separación entre telemetría y audio.
- **Seguridad, auditoría, actualización y diagnóstico:** `security.py`, `tools/audit_service.py`,
  `provider_status.py`, `secure_update.py`, `health.py`, `action_history_ipc.py`.

La CLI (`cli_entrypoint.py`) expone ingeniería interactiva (`/status`, `/resume`, `/cancel`,
`/new`), consultas por stdin y comandos para daemon/recuperación/soak, doctor, verificación de
auditoría, administración de plugins/habilidades/capacidades, preparación/diagnóstico de MCP,
importación explícita de credenciales, distribución, firma de canales y calificaciones.
Los `probe-nvidia*` e importadores NVIDIA permanecen como comandos heredados retirados
(`cli.py:_retired_nvidia_command`); no son capacidades productivas para reconstruir.

## Garantías que no se pueden perder al reescribir

1. El namespace técnico `jarvis`, identificadores de bundle, rutas y cuentas de Keychain deben
   conservar su continuidad. Un inventario funcional no autoriza renombrar datos del dueño.
2. El dueño escribe contraseñas, concede TCC y aprueba decisiones. Ni REA ni un motor nuevo
   pueden conceder esos permisos por él. La aprobación por voz queda restringida a lectura;
   una palabra reconocida no puede autorizar una mutación.
3. Una única autoridad final valida propuesta → autorización vinculada → efecto → resultado.
   No se puede tener un ejecutor externo que rodee el broker o acepte permisos implícitos.
4. Esquemas cerrados, capacidades/roles, digest de argumentos, firma, expiración, consumo único,
   revocación y rechazo de replay. Modo plan permanece de lectura y no consume autorizaciones.
5. Los metadatos de modo plan, proyecto y conversación temporal proceden del daemon; se eliminan
   si llegan del cliente. Un cliente IPC no firmado no puede hablar como el dueño.
6. La cancelación debe detener el trabajo y esperar el cierre antes de admitir reutilización del
   recurso. Un turno cancelado en cola no puede cerrar el turno activo. No repetir un efecto
   incierto ni anunciar éxito por haber producido texto.
7. Conservación y migración verificables de memoria cifrada, namespaces, índices y checkpoints;
   no mezclar embeddings de distinta identidad. Borrado y olvido deben invalidar recuperación.
8. Un chat temporal no guarda ni consulta memoria personal ni el historial de acciones. El
   historial del dueño y `auth.json` están fuera de la investigación y pruebas del agente.
9. Escritura privada mediante `write_private_json`, rutas acotadas, no enlaces a secretos,
   Keychain y registros de metadatos mínimos; auditoría íntegra sin prompts ni capturas.
10. Contenido web, archivos, MCP, habilidades y salidas de modelos son datos no fiables;
    no amplían herramientas, destinos, cuentas ni permisos.
11. Codex/ChatGPT conserva inicio oficial y estado propio. Producción no envía inferencia,
    memoria ni audio a NVIDIA. Ollama, otras APIs y audio requieren elección/configuración
    independiente, sin fallback silencioso.
12. La app firmada sigue siendo la entrada para pruebas reales de IPC. Sus llamadas terminan
    en menos de 30 s; las tareas largas usan estados/espera/cancelación. Debe respetarse tamaño,
    empaquetado autocontenido y compatibilidad del contrato nativo.

## Pendientes y deuda que deben acompañar la reconstrucción

La lista vigente es `docs/V1_PENDIENTES.md`. Este inventario no cambia sus casillas ni convierte
una certificación histórica en certificación de la nueva variante.

- Insertar una nueva línea al final de TextEdit conservando y verificando el contenido anterior.
- Negativas intermitentes de selección de herramientas documentadas en
  `docs/quality/CODEX_CONNECTION_20261023.md`; causa aún no confirmada.
- Proveedor de audio y notas de voz sin configurar; certificación física de voz, ruido y hablante.
- Nuevas conexiones Ollama/OpenAI API/Anthropic bajo elección explícita; no restaurar NVIDIA.
- Verificación de hablante real, eliminación de pistas «sí/ok» y frase que nombre la acción:
  pendientes históricos de seguridad de voz; la restricción de lectura actual se conserva.
- Auditoría de borrados de chats/proyectos mediante metadatos mínimos (R2-9).
- Peso del repositorio y artefactos de cobertura, copia generada de localización (R2-7).
- Legibilidad de detalles de decisiones (R2-16), vacío de archivados (R2-4) y buscador (R2-5).
- Accesibilidad real con VoiceOver/teclado (P1-12); etiquetas AX sintéticas no bastan.
- Developer ID/notarización, canal de actualización publicado y prueba entre versiones
  notarizadas (P0-1/P0-2), dependientes del dueño.
- Reducción de distribución/daemon, inglés, app compañera iPhone y recorrido físico final.

Las notas antiguas sobre NVIDIA exclusivo o cifras de pruebas anteriores son contexto histórico.
La fuente de verdad de la conexión vigente es AGENTS y el código de arranque actual.

## Matriz mínima de paridad antes de sustituir el núcleo actual

| Recorrido | Datos de prueba y evidencia requerida |
| --- | --- |
| Texto, imagen, adjuntos y proyectos | Conversación temporal y archivos sintéticos; respuesta, destino, contexto y límites observados. |
| Memoria | Guardar, reiniciar, recuperar, aislar y olvidar un literal sintético; evidencia del efecto sobre consultas posteriores. |
| Herramienta de lectura | Consultar batería o fixture MCP; recibo de herramienta y valor observado, no sólo frase del modelo. |
| Escritura con permiso | Documento desechable; aprobación del dueño, literal observado, otro chat y revocación que impida el efecto. |
| Navegador | Sitio/fixture aprobado por tarea, snapshot fresco, una acción acotada y postcondición; rechazo de snapshot caducado. |
| Cancelación/reinicio | Cancelar durante respuesta, aprobación y efecto controlado; siguiente turno operativo sin reenvío de efectos. |
| Tarea proactiva | Una programación sintética con entrega recuperable, pausa y solicitud de aprobación cuando proceda. |
| Canal/dispositivo | Entorno de prueba autorizado de Telegram/MCP/TV/móvil; conexión y scope exactos, rechazo de identidad ajena. |
| Voz | Captura → transcripción → razonamiento → síntesis → interrupción, con proveedor elegido y mediciones físicas del dueño cuando correspondan. |
| Instalación y privacidad | App firmada, daemon/IPC, permisos ausentes o revocados, temporal sin persistencia, logs sin contenido privado. |

Las pruebas automatizadas existentes de cada dominio son punto de partida (`tests/test_codex_provider.py`,
`test_computer_*.py`, `test_memory_*.py`, `test_mcp_*.py`, `test_browser_task*.py`,
`test_approval_signing.py`, `test_ipc_*.py`, `test_telegram*.py`, `test_scheduling.py`,
`test_goals.py`, `test_projects.py`, `test_plugins.py`, `test_skills.py`, `test_audio_service.py`,
`test_speech_service.py`, `test_engineering*.py`, `test_secure_update*.py`). Su existencia no
certifica por sí sola la variante nueva. No se ejecutaron ni se añadieron pruebas de producto en
este inventario de documentación; se verificaron sintaxis AST, cuentas y rutas de la captura.

## Censo exacto de fuentes Python propias

### (raíz) (96)

- `src/jarvis_core/__init__.py`
- `src/jarvis_core/acceptance_benchmark.py`
- `src/jarvis_core/action_history.py`
- `src/jarvis_core/action_history_ipc.py`
- `src/jarvis_core/activity.py`
- `src/jarvis_core/application_qualification.py`
- `src/jarvis_core/approval_signing.py`
- `src/jarvis_core/async_tasks.py`
- `src/jarvis_core/attention.py`
- `src/jarvis_core/audit_anchor.py`
- `src/jarvis_core/biometric_training_service.py`
- `src/jarvis_core/browser_preferences.py`
- `src/jarvis_core/browser_qualification.py`
- `src/jarvis_core/build_info.py`
- `src/jarvis_core/bundle_paths.py`
- `src/jarvis_core/capability_blueprints.py`
- `src/jarvis_core/capability_learning.py`
- `src/jarvis_core/capability_review_gate.py`
- `src/jarvis_core/capability_reviews.py`
- `src/jarvis_core/chat_attachments.py`
- `src/jarvis_core/cli.py`
- `src/jarvis_core/cli_entrypoint.py`
- `src/jarvis_core/completion_boundary.py`
- `src/jarvis_core/computer_app_grants.py`
- `src/jarvis_core/config.py`
- `src/jarvis_core/contracts.py`
- `src/jarvis_core/conversation_quality.py`
- `src/jarvis_core/dialogue.py`
- `src/jarvis_core/dialogue_challenges.py`
- `src/jarvis_core/dialogue_stability.py`
- `src/jarvis_core/distribution_archive.py`
- `src/jarvis_core/distribution_release_qualification.py`
- `src/jarvis_core/dot.py`
- `src/jarvis_core/engineering.py`
- `src/jarvis_core/engineering_cli.py`
- `src/jarvis_core/engineering_input.py`
- `src/jarvis_core/engineering_terminal.py`
- `src/jarvis_core/evaluation.py`
- `src/jarvis_core/extensions_service.py`
- `src/jarvis_core/failure_boundary.py`
- `src/jarvis_core/feedback.py`
- `src/jarvis_core/goals.py`
- `src/jarvis_core/google_account_settings.py`
- `src/jarvis_core/health.py`
- `src/jarvis_core/inference_service.py`
- `src/jarvis_core/inference_settings.py`
- `src/jarvis_core/job_admission.py`
- `src/jarvis_core/job_authorization.py`
- `src/jarvis_core/job_completion.py`
- `src/jarvis_core/job_contracts.py`
- `src/jarvis_core/job_execution.py`
- `src/jarvis_core/job_failures.py`
- `src/jarvis_core/job_graph.py`
- `src/jarvis_core/job_interactions.py`
- `src/jarvis_core/job_ipc.py`
- `src/jarvis_core/job_lifecycle.py`
- `src/jarvis_core/job_metrics.py`
- `src/jarvis_core/job_state.py`
- `src/jarvis_core/job_tasks.py`
- `src/jarvis_core/job_tool_presenter.py`
- `src/jarvis_core/job_tool_runtime.py`
- `src/jarvis_core/jobs.py`
- `src/jarvis_core/langgraph_compat.py`
- `src/jarvis_core/long_horizon_reliability.py`
- `src/jarvis_core/macos_qualification.py`
- `src/jarvis_core/mascot.py`
- `src/jarvis_core/models.py`
- `src/jarvis_core/owner_language.py`
- `src/jarvis_core/performance_profiler.py`
- `src/jarvis_core/personality.py`
- `src/jarvis_core/pilot_release_qualification.py`
- `src/jarvis_core/privacy.py`
- `src/jarvis_core/production_workflows.py`
- `src/jarvis_core/projects.py`
- `src/jarvis_core/provider_status.py`
- `src/jarvis_core/release_scope.py`
- `src/jarvis_core/request_intent.py`
- `src/jarvis_core/runtime_preflight.py`
- `src/jarvis_core/runtime_state.py`
- `src/jarvis_core/scheduling.py`
- `src/jarvis_core/secrets.py`
- `src/jarvis_core/secure_update.py`
- `src/jarvis_core/secure_update_feed.py`
- `src/jarvis_core/secure_update_qualification.py`
- `src/jarvis_core/security.py`
- `src/jarvis_core/self_contained_release_qualification.py`
- `src/jarvis_core/settings_service.py`
- `src/jarvis_core/settings_store.py`
- `src/jarvis_core/skill_flow_benchmark.py`
- `src/jarvis_core/skill_flow_live.py`
- `src/jarvis_core/speech.py`
- `src/jarvis_core/speech_pcm.py`
- `src/jarvis_core/standing_grants.py`
- `src/jarvis_core/style.py`
- `src/jarvis_core/tcc_privacy.py`
- `src/jarvis_core/voice_qualification.py`

### audio (3)

- `src/jarvis_core/audio/__init__.py`
- `src/jarvis_core/audio/contracts.py`
- `src/jarvis_core/audio/service.py`

### brain (15)

- `src/jarvis_core/brain/__init__.py`
- `src/jarvis_core/brain/agent_graph.py`
- `src/jarvis_core/brain/behavior_tree.py`
- `src/jarvis_core/brain/errors.py`
- `src/jarvis_core/brain/inference_client.py`
- `src/jarvis_core/brain/nodes/__init__.py`
- `src/jarvis_core/brain/nodes/reflector.py`
- `src/jarvis_core/brain/nvidia_client.py`
- `src/jarvis_core/brain/nvidia_dialogue_guard.py`
- `src/jarvis_core/brain/nvidia_goal.py`
- `src/jarvis_core/brain/nvidia_summary.py`
- `src/jarvis_core/brain/router.py`
- `src/jarvis_core/brain/routing.py`
- `src/jarvis_core/brain/vision_fallback.py`
- `src/jarvis_core/brain/vision_processor.py`

### ipc (6)

- `src/jarvis_core/ipc/__init__.py`
- `src/jarvis_core/ipc/client.py`
- `src/jarvis_core/ipc/framing.py`
- `src/jarvis_core/ipc/peer_identity.py`
- `src/jarvis_core/ipc/protocol.py`
- `src/jarvis_core/ipc/server.py`

### mcp (14)

- `src/jarvis_core/mcp/__init__.py`
- `src/jarvis_core/mcp/browser_session.py`
- `src/jarvis_core/mcp/browser_tasks.py`
- `src/jarvis_core/mcp/client.py`
- `src/jarvis_core/mcp/collections.py`
- `src/jarvis_core/mcp/discovery.py`
- `src/jarvis_core/mcp/google_workspace.py`
- `src/jarvis_core/mcp/home_assistant.py`
- `src/jarvis_core/mcp/host_manager.py`
- `src/jarvis_core/mcp/integrations.py`
- `src/jarvis_core/mcp/local_peers.py`
- `src/jarvis_core/mcp/result_text.py`
- `src/jarvis_core/mcp/review.py`
- `src/jarvis_core/mcp/setup.py`

### memory (40)

- `src/jarvis_core/memory/__init__.py`
- `src/jarvis_core/memory/capacity.py`
- `src/jarvis_core/memory/codec.py`
- `src/jarvis_core/memory/contracts.py`
- `src/jarvis_core/memory/conversation_codec.py`
- `src/jarvis_core/memory/conversation_context.py`
- `src/jarvis_core/memory/conversation_evidence.py`
- `src/jarvis_core/memory/conversation_migration.py`
- `src/jarvis_core/memory/conversation_repository.py`
- `src/jarvis_core/memory/conversation_search.py`
- `src/jarvis_core/memory/conversations.py`
- `src/jarvis_core/memory/crypto.py`
- `src/jarvis_core/memory/embedding.py`
- `src/jarvis_core/memory/errors.py`
- `src/jarvis_core/memory/graph_embedding_repository.py`
- `src/jarvis_core/memory/graph_extractor.py`
- `src/jarvis_core/memory/graph_read_repository.py`
- `src/jarvis_core/memory/graph_repository.py`
- `src/jarvis_core/memory/graph_search.py`
- `src/jarvis_core/memory/graph_service.py`
- `src/jarvis_core/memory/item_repository.py`
- `src/jarvis_core/memory/langgraph_checkpoint.py`
- `src/jarvis_core/memory/migrations.py`
- `src/jarvis_core/memory/profile.py`
- `src/jarvis_core/memory/records.py`
- `src/jarvis_core/memory/retrieval.py`
- `src/jarvis_core/memory/retrieval_planning.py`
- `src/jarvis_core/memory/schema.py`
- `src/jarvis_core/memory/search_repository.py`
- `src/jarvis_core/memory/semantic_profile.py`
- `src/jarvis_core/memory/service.py`
- `src/jarvis_core/memory/shared_context_ranking.py`
- `src/jarvis_core/memory/shared_conversation_context.py`
- `src/jarvis_core/memory/social.py`
- `src/jarvis_core/memory/spotlight_sync.py`
- `src/jarvis_core/memory/sqlite.py`
- `src/jarvis_core/memory/storage.py`
- `src/jarvis_core/memory/task_checkpoint.py`
- `src/jarvis_core/memory/vector_db.py`
- `src/jarvis_core/memory/visibility.py`

### orchestration (23)

- `src/jarvis_core/orchestration/__init__.py`
- `src/jarvis_core/orchestration/authorization_nodes.py`
- `src/jarvis_core/orchestration/clarification.py`
- `src/jarvis_core/orchestration/conversation_window.py`
- `src/jarvis_core/orchestration/direct_actions.py`
- `src/jarvis_core/orchestration/engineering_dialogue.py`
- `src/jarvis_core/orchestration/execution_nodes.py`
- `src/jarvis_core/orchestration/graph.py`
- `src/jarvis_core/orchestration/inference.py`
- `src/jarvis_core/orchestration/memory_nodes.py`
- `src/jarvis_core/orchestration/notes.py`
- `src/jarvis_core/orchestration/nvidia_dialogue.py`
- `src/jarvis_core/orchestration/planning_nodes.py`
- `src/jarvis_core/orchestration/private_context.py`
- `src/jarvis_core/orchestration/response_rendering.py`
- `src/jarvis_core/orchestration/routing.py`
- `src/jarvis_core/orchestration/routing_nodes.py`
- `src/jarvis_core/orchestration/semantic_reference.py`
- `src/jarvis_core/orchestration/semantic_tools.py`
- `src/jarvis_core/orchestration/skill_selection.py`
- `src/jarvis_core/orchestration/state.py`
- `src/jarvis_core/orchestration/synthesis_nodes.py`
- `src/jarvis_core/orchestration/task_progress.py`

### plugins (8)

- `src/jarvis_core/plugins/__init__.py`
- `src/jarvis_core/plugins/context7.py`
- `src/jarvis_core/plugins/contracts.py`
- `src/jarvis_core/plugins/foreign.py`
- `src/jarvis_core/plugins/foreign_service.py`
- `src/jarvis_core/plugins/runtime.py`
- `src/jarvis_core/plugins/service.py`
- `src/jarvis_core/plugins/store.py`

### providers (9)

- `src/jarvis_core/providers/__init__.py`
- `src/jarvis_core/providers/apple_embedding.py`
- `src/jarvis_core/providers/base.py`
- `src/jarvis_core/providers/codex.py`
- `src/jarvis_core/providers/errors.py`
- `src/jarvis_core/providers/inference_metrics.py`
- `src/jarvis_core/providers/mlx_provider.py`
- `src/jarvis_core/providers/nvidia.py`
- `src/jarvis_core/providers/nvidia_asr.py`

### runtime (4)

- `src/jarvis_core/runtime/__init__.py`
- `src/jarvis_core/runtime/daemon.py`
- `src/jarvis_core/runtime/frozen_entrypoint.py`
- `src/jarvis_core/runtime/lifecycle.py`

### skills (7)

- `src/jarvis_core/skills/__init__.py`
- `src/jarvis_core/skills/builtins.py`
- `src/jarvis_core/skills/contracts.py`
- `src/jarvis_core/skills/importer.py`
- `src/jarvis_core/skills/policy.py`
- `src/jarvis_core/skills/registry.py`
- `src/jarvis_core/skills/teach.py`

### telegram (8)

- `src/jarvis_core/telegram/__init__.py`
- `src/jarvis_core/telegram/client.py`
- `src/jarvis_core/telegram/configuration.py`
- `src/jarvis_core/telegram/experience.py`
- `src/jarvis_core/telegram/journal.py`
- `src/jarvis_core/telegram/media.py`
- `src/jarvis_core/telegram/presentation.py`
- `src/jarvis_core/telegram/service.py`

### tools (22)

- `src/jarvis_core/tools/__init__.py`
- `src/jarvis_core/tools/android_automation.py`
- `src/jarvis_core/tools/audit.py`
- `src/jarvis_core/tools/audit_service.py`
- `src/jarvis_core/tools/automation_errors.py`
- `src/jarvis_core/tools/background_automation.py`
- `src/jarvis_core/tools/broker.py`
- `src/jarvis_core/tools/chrome_cdp.py`
- `src/jarvis_core/tools/computer.py`
- `src/jarvis_core/tools/computer_postconditions.py`
- `src/jarvis_core/tools/computer_relay.py`
- `src/jarvis_core/tools/computer_workflow.py`
- `src/jarvis_core/tools/confirmations.py`
- `src/jarvis_core/tools/defaults.py`
- `src/jarvis_core/tools/device_config.py`
- `src/jarvis_core/tools/execution.py`
- `src/jarvis_core/tools/ios_bridge.py`
- `src/jarvis_core/tools/native_receipts.py`
- `src/jarvis_core/tools/silent_executor.py`
- `src/jarvis_core/tools/smart_tv.py`
- `src/jarvis_core/tools/verification.py`
- `src/jarvis_core/tools/web.py`
