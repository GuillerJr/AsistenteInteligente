# Pulido 3 — Automatización de macOS

Base: `cdbb1ba`. Fecha: 2026-09-11. Endurece la automatización acotada existente sin ampliar
permisos, habilitar shell libre ni cambiar la inferencia `nvidia_only` del CLI.

## Defectos reproducidos y correcciones

Se reprodujeron **17 fallos** antes de modificar la implementación. La cobertura añadida alcanza
**40 casos** y conserva las pruebas previas de permisos, contexto visual y recuperación.

| Riesgo | Contrato corregido |
| --- | --- |
| Cancelación durante activación, captura o acción liberaba la exclusión con el worker aún activo | `run_blocking_owned` espera su salida, incluso ante cancelaciones repetidas, antes de liberar la sesión. |
| Orden cancelada/caducada ocupaba la cola y rechazaba la siguiente | Se retira al terminar la petición; los plazos se comprueban también antes de entregar y completar. |
| Cierre del enlace dejaba consumidores esperando hasta 20 segundos | El cierre despierta todos los consumidores y libera órdenes y respuestas pendientes. |
| Resultado de otra llamada aceptado como éxito | Se vinculan `call_id` y herramienta antes de recuperar, reflejar o avanzar; los IDs duplicados se rechazan antes de ejecutar. |
| `verified=1`, texto o evidencia ausente contaban como éxito visual | Se exige `verified is True`, `status=completed` y ausencia de error en planificación, ejecución aprobada, métricas y presentación. |
| Reintentos tras intervención del usuario o ejecución parcial | Solo se permite recuperación ante `uncertain_state`, sin error y con cero pasos ejecutados; el historial no puede ampliar esta autorización. |

Las recargas y reevaluaciones exigen una confirmación consumida. La corrección de modales exige
que su resultado pertenezca a la acción autorizada. La aplicación objetivo explícita del helper
tiene precedencia sobre la aplicación en primer plano: una coincidencia del primer plano no puede
encubrir una acción sobre otra aplicación.

La evidencia nativa usa booleanos estrictos y comprueba que `state_changed` coincida con la
comparación de hashes AX. Una lectura estable puede ser válida; no se confunde «se leyó el estado»
con «se completó el objetivo». Foco y reemplazo de texto conservan su comprobación posterior de
estado aunque el helper no devuelva `action_verification`. El enlace rechaza NaN e infinitos.

## Cancelación y límites

Cancelar una coroutine no detiene un hilo que ya ejecuta una llamada bloqueante. El wrapper
conserva la propiedad de ese trabajo hasta que retorna o alcanza su timeout propio, recoge fallos
tardíos y vuelve a propagar la cancelación original. Si el trabajo sigue en la cola del pool,
omite la llamada cuando obtiene un worker. No crea un segundo scheduler ni cancela otros usuarios
del pool. Las extensiones síncronas deben imponer sus propios límites de I/O.

La cancelación **no revierte un evento ya enviado a macOS** ni garantiza que una aplicación externa
deshaga sus efectos. Una pérdida del helper o de su enlace no acredita éxito. No hay reintento
automático de una acción cuyo estado final sea desconocido. El presupuesto de una tarea puede
incluir el drenaje de su última llamada bloqueante; no se promete cancelación instantánea.

La decisión de conservar y drenar el worker sigue los contratos de
[cancelación de asyncio](https://docs.python.org/3/library/asyncio-task.html#shielding-from-cancellation)
y de [futuros ya en ejecución](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Future.cancel).

## Verificación reproducible

```bash
.venv/bin/pytest -q tests/test_owned_automation.py tests/test_computer_use.py \
  tests/test_computer_relay.py tests/test_agent_graph.py tests/test_jobs.py \
  tests/test_application_qualification.py tests/test_evolutionary_runtime.py
.venv/bin/ruff check src tests
.venv/bin/pytest -q
./script/aegis.sh acceptance-benchmark
./script/aegis.sh production-workflows
./script/aegis.sh long-horizon-reliability
```

La suite completa necesita sockets Unix locales. Los gates obligatorios del commit y del push
añaden Swift, paquete firmado, 25 contratos de aceptación, 20 flujos de producción y 400 ejecuciones
prolongadas con 1160 checkpoints. No se omiten los hooks.

Resultado determinista de esta revisión: **1461 pruebas Python correctas, 17 opt-in omitidas**;
177 pruebas correctas en el conjunto dirigido. Aceptación: 25/25; producción: 20/20;
fiabilidad: 400/400 ejecuciones, 1160/1160 checkpoints y cero tareas asíncronas huérfanas.

La comprobación de la app instalada se realiza después de empaquetar esta misma revisión:

```bash
AEGIS_BUILD_MLX=0 ./script/menu_bar_service.sh install
./script/jarvis_beta.sh check
./script/p9_browser_gate.sh
```

El último gate incluye salud e IPC, una aplicación AppKit desechable firmada, captura/AX, escritura
literal, pulsación, foco e invariancia del puntero, y Chrome con perfil efímero, CDP de loopback y
salida a Internet bloqueada. No usa correo, calendarios, contactos, documentos ni perfiles
personales. Sus fixtures temporales se eliminan al finalizar. Un fallo de permisos bloquea la
calificación; no se evade TCC ni se conceden permisos silenciosamente.

Este bloque no certifica automatización arbitraria ni todas las aplicaciones de macOS. Conserva
los límites explícitos de acciones, pasos, aplicaciones sensibles y aprobación de un solo uso.
