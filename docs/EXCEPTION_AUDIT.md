# Auditoría de fronteras de excepción — 21 de octubre de 2026

Se revisaron los 88 `except Exception` originales. Cinco salieron del paquete instalado
junto con el cerebro híbrido (`tests/historical`). Ocho interiores se estrecharon: cinco
parsers semánticos capturan `RecursionError`/`TypeError`/`ValueError`, los dos adaptadores Keychain usan
sus errores declarados y SQLite limita la carga de extensión a errores de SQLite/SO.
La selección de skills separa además el callback remoto del parseo/cache local.

Hay dos fronteras nuevas y explícitas: `optional_completion`, que agrupa el fallo de los
modelos opcionales, y la persistencia del feedback de una respuesta. El resultado es
77 capturas generales, inventariadas abajo. No se sustituyeron por tuplas universales
de excepciones: cada captura conservada protege una entrada externa, un trabajador,
un callback intercambiable o la salida de una transacción/cancelación.

Todas registran exclusivamente el tipo del error (directamente, en la política de
fallos del job, o mediante `failure_boundary.record_boundary_error`). Nunca se pasa
`str(error)`, `repr(error)`, `exc_info` ni el traceback al log. Los códigos públicos ya
validados se mantienen; no son mensajes arbitrarios del proveedor. `CancelledError`,
`KeyboardInterrupt` y `SystemExit` siguen fuera de `Exception`.

`tests/test_failure_boundaries.py` comprueba que ningún catch general quede sin
registro tipado, que contenido sintético privado no aparezca en logs y que se conserve
la cancelación. Las regresiones de jobs, scheduler, Telegram, IPC, herramientas y
memoria comprueban los estados de fallo y el cierre de las aprobaciones.

## Inventario de capturas generales conservadas

Rutas relativas a `src/aegis_core`. Las líneas son una ayuda de navegación; el nombre de
la función identifica la frontera aunque cambie su posición.

| Archivo:línea | Frontera | Motivo |
| --- | --- | --- |
| `acceptance_benchmark.py:202` | `JarvisAcceptanceBenchmark.run` | Entrada de proceso o caso aislado: devuelve estado de fallo, nunca contenido de excepción. |
| `async_tasks.py:43` | `run_blocking_owned` | Limpieza/transacción: drenar trabajo o revertir y conservar la excepción original. |
| `biometric_training_service.py:438` | `BiometricTrainingService.run` | Trabajador/entrega: aislar el fallo para conservar el servicio y futuras ejecuciones. |
| `brain/behavior_tree.py:109` | `Condition.tick` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `brain/behavior_tree.py:153` | `Action.tick` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `completion_boundary.py:21` | `optional_completion` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `goals.py:451` | `GoalRunner.step` | Trabajador/entrega: aislar el fallo para conservar el servicio y futuras ejecuciones. |
| `goals.py:474` | `GoalRunner._send` | Trabajador/entrega: aislar el fallo para conservar el servicio y futuras ejecuciones. |
| `google_account_settings.py:116` | `GoogleAccountAuthorization.run` | Frontera IPC/OAuth/operación: fallo cerrado y diagnóstico tipado. |
| `google_account_settings.py:190` | `GoogleAccountAuthorization.callback` | Frontera IPC/OAuth/operación: fallo cerrado y diagnóstico tipado. |
| `health.py:147` | `HealthIpcService.handle` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `health.py:152` | `HealthIpcService.handle` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `health.py:157` | `HealthIpcService.handle` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `ipc/server.py:386` | `AegisDaemon._peer_may_call` | Frontera IPC/OAuth/operación: fallo cerrado y diagnóstico tipado. |
| `ipc/server.py:409` | `AegisDaemon._peer_may_call` | Frontera IPC/OAuth/operación: fallo cerrado y diagnóstico tipado. |
| `ipc/server.py:486` | `AegisDaemon._dispatch` | Frontera IPC/OAuth/operación: fallo cerrado y diagnóstico tipado. |
| `ipc/server.py:594` | `AegisDaemon._record_protocol_violation` | Frontera IPC/OAuth/operación: fallo cerrado y diagnóstico tipado. |
| `job_admission.py:228` | `JobAdmissionCoordinator.apply_owner_feedback` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `job_execution.py:391` | `JobExecutionPipeline._record_exchange` | Limpieza/transacción: drenar trabajo o revertir y conservar la excepción original. |
| `job_lifecycle.py:311` | `JobLifecycleCoordinator.build_metrics` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `job_lifecycle.py:371` | `JobLifecycleCoordinator._record_evaluation` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `job_tool_runtime.py:128` | `JobToolRuntime.consume_confirmation` | Ejecución o aprobación: estado terminal/cancelación; jamás autorizar por un fallo. |
| `jobs.py:264` | `SwarmJobManager.record_owner_feedback` | Ejecución o aprobación: estado terminal/cancelación; jamás autorizar por un fallo. |
| `jobs.py:516` | `SwarmJobManager._run` | Ejecución o aprobación: estado terminal/cancelación; jamás autorizar por un fallo. |
| `jobs.py:678` | `SwarmJobManager._run_approved_tool` | Ejecución o aprobación: estado terminal/cancelación; jamás autorizar por un fallo. |
| `jobs.py:941` | `SwarmJobManager._schedule_request_cleanup_locked.cleanup` | Ejecución o aprobación: estado terminal/cancelación; jamás autorizar por un fallo. |
| `jobs.py:1030` | `SwarmJobManager._schedule_request_cleanup_locked.cleanup` (proyección de actividad) | Aislar el fallo del historial opcional sin saltarse la limpieza de recursos de la solicitud; registra solo el tipo y conserva la cancelación. |
| `long_horizon_reliability.py:221` | `JarvisLongHorizonReliabilityBenchmark.run` | Entrada de proceso o caso aislado: devuelve estado de fallo, nunca contenido de excepción. |
| `mcp/browser_session.py:568` | `ReviewedBrowserSession.execute` | Herramienta externa/seguridad: invalidar estado y denegar ante fallo. |
| `mcp/browser_tasks.py:626` | `BrowserTaskService._drop` | Herramienta externa/seguridad: invalidar estado y denegar ante fallo. |
| `mcp/browser_tasks.py:831` | `BrowserTaskService._handle` | Herramienta externa/seguridad: invalidar estado y denegar ante fallo. |
| `memory/graph_service.py:227` | `MemoryDecayWorker.run` | Trabajador/entrega: aislar el fallo para conservar el servicio y futuras ejecuciones. |
| `memory/graph_service.py:242` | `MemoryDecayWorker._record` | Trabajador/entrega: aislar el fallo para conservar el servicio y futuras ejecuciones. |
| `memory/migrations.py:115` | `EncryptedMemoryMigrator.migrate_v4_to_v5` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `memory/migrations.py:194` | `EncryptedMemoryMigrator.migrate_v5_to_v6` | Limpieza/transacción: drenar trabajo o revertir y conservar la excepción original. |
| `memory/migrations.py:250` | `EncryptedMemoryMigrator.migrate_v9_to_v10` | Limpieza/transacción: drenar trabajo o revertir y conservar la excepción original. |
| `memory/migrations.py:342` | `EncryptedMemoryMigrator.migrate_v6_to_v7` | Limpieza/transacción: drenar trabajo o revertir y conservar la excepción original. |
| `memory/migrations.py:369` | `EncryptedMemoryMigrator.migrate_v6_to_v7` | Limpieza/transacción: drenar trabajo o revertir y conservar la excepción original. |
| `memory/schema.py:194` | `create_current_schema` | Limpieza/transacción: drenar trabajo o revertir y conservar la excepción original. |
| `memory/schema.py:270` | `migrate_v3_to_v4` | Limpieza/transacción: drenar trabajo o revertir y conservar la excepción original. |
| `memory/spotlight_sync.py:69` | `SpotlightGraphSync.run` | Trabajador/entrega: aislar el fallo para conservar el servicio y futuras ejecuciones. |
| `memory/sqlite.py:1189` | `SQLiteMemoryStore._notify_graph_changed` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `memory/sqlite.py:1214` | `SQLiteMemoryStore._mark_compromised` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `memory/vector_db.py:327` | `EmbeddingBackfillWorker.run` | Trabajador/entrega: aislar el fallo para conservar el servicio y futuras ejecuciones. |
| `memory/vector_db.py:368` | `EmbeddingBackfillWorker._record_event` | Trabajador/entrega: aislar el fallo para conservar el servicio y futuras ejecuciones. |
| `orchestration/graph.py:232` | `build_swarm_graph.remote_redaction_for` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `orchestration/notes.py:92` | `create_notes.owner_voice` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `orchestration/planning_nodes.py:580` | `create_specialist_node.specialist_node.analyze_advisor` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `orchestration/skill_selection.py:83` | `create_skill_selection.semantic_skills` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `performance_profiler.py:584` | `PerformanceProfiler._collect_sync` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `plugins/store.py:306` | `PluginStore._legacy_seal_matches` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `production_workflows.py:535` | `JarvisProductionWorkflowBenchmark.run` | Entrada de proceso o caso aislado: devuelve estado de fallo, nunca contenido de excepción. |
| `provider_status.py:29` | `ProviderStatusIpcService._probe` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `providers/mlx_provider.py:67` | `MLXWhisperTranscriber.transcribe_pcm_s16le` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `runtime/frozen_entrypoint.py:33` | `_self_test` | Entrada de proceso o caso aislado: devuelve estado de fallo, nunca contenido de excepción. |
| `runtime/frozen_entrypoint.py:46` | `_self_test` | Entrada de proceso o caso aislado: devuelve estado de fallo, nunca contenido de excepción. |
| `runtime/lifecycle.py:59` | `BackgroundTaskSupervisor._task_finished` | Trabajador/entrega: aislar el fallo para conservar el servicio y futuras ejecuciones. |
| `scheduling.py:691` | `TaskScheduler._run` | Trabajador/entrega: aislar el fallo para conservar el servicio y futuras ejecuciones. |
| `scheduling.py` | `run_unattended` (dos fronteras de observadores posteriores a admisión) | Un fallo del callback de admisión, notificación o sondeo cancela y drena el job que ya se creó antes de propagar el fallo; conserva su identidad y no deja una aprobación activa ni repite el trabajo. Si la admisión acaba después de cancelar, un fallo del callback no impide limpiar ni sustituye esa cancelación. Ambas registran solo `error_type`. |
| `scheduling.py:763` | `TaskScheduler._should_deliver` | Trabajador/entrega: aislar el fallo para conservar el servicio y futuras ejecuciones. |
| `scheduling.py:772` | `TaskScheduler._send` | Trabajador/entrega: aislar el fallo para conservar el servicio y futuras ejecuciones. |
| `secure_update.py:1107` | `SecureUpdateInstaller.install` | Limpieza/transacción: drenar trabajo o revertir y conservar la excepción original. |
| `secure_update.py:1111` | `SecureUpdateInstaller.install` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `secure_update.py:1131` | `SecureUpdateInstaller.install` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `settings_service.py:151` | `SettingsIpcService.status` | Frontera IPC/OAuth/operación: fallo cerrado y diagnóstico tipado. |
| `settings_service.py:205` | `SettingsIpcService._launch_operation.run` | Frontera IPC/OAuth/operación: fallo cerrado y diagnóstico tipado. |
| `settings_service.py:402` | `SettingsIpcService.handle` | Frontera IPC/OAuth/operación: fallo cerrado y diagnóstico tipado. |
| `skill_flow_benchmark.py:634` | `SkillFlowBenchmark.run_case` | Entrada de proceso o caso aislado: devuelve estado de fallo, nunca contenido de excepción. |
| `skill_flow_benchmark.py:1226` | `_run_catalogue_case` | Entrada de proceso o caso aislado: devuelve estado de fallo, nunca contenido de excepción. |
| `skill_flow_live.py:326` | `_run_case` | Entrada de proceso o caso aislado: devuelve estado de fallo, nunca contenido de excepción. |
| `skill_flow_live.py:533` | `main` | Entrada de proceso o caso aislado: devuelve estado de fallo, nunca contenido de excepción. |
| `skills/registry.py:344` | `SkillRegistry._plugin_is_available` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `skills/teach.py:123` | `SkillTeachHandlers._create` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
| `speech.py:301` | `SpeechStreamManager._close_iterator` | Limpieza/transacción: drenar trabajo o revertir y conservar la excepción original. |
| `speech.py:355` | `SpeechArtifactStore.create` | Limpieza/transacción: drenar trabajo o revertir y conservar la excepción original. |
| `telegram/media.py:132` | `worker` | Entrada de proceso o caso aislado: devuelve estado de fallo, nunca contenido de excepción. |
| `tools/broker.py:60` | `PolicyContext.is_security_compromised` | Herramienta externa/seguridad: invalidar estado y denegar ante fallo. |
| `tools/audit.py:200` | `HashChainAuditLog._notify_activity` | Callback opcional después de persistir la auditoría: un fallo del historial consultable no altera la cadena de seguridad ni el resultado de la herramienta; registra solo el tipo. |
| `tools/chrome_cdp.py:516` | `ChromeCDPController._owned_page` | Limpieza/transacción: drenar trabajo o revertir y conservar la excepción original. |
| `tools/computer_relay.py:113` | `ComputerCommandRelay.request_sync` | Callback/extensión externa: resultado opcional o fallo cerrado; cancelar sigue propagándose. |
