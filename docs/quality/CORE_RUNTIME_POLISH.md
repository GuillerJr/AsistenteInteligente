# Pulido 1 — Núcleo y ejecución

La revisión parte de `39a78d3`. Este bloque corrige cierre, cancelación y recuperación del runtime.
No certifica voz ni amplía el alcance 1.0.

| Problema encontrado | Cambio verificable |
|---|---|
| Un handler podía seguir vivo tras cerrar el listener | El servidor posee y drena todas las conexiones antes de cerrar |
| Un frame incompleto quedaba esperando el timeout | El cierre aborta inmediatamente su transporte |
| Dos cierres concurrentes podían informar éxito antes de limpiar | Todos los solicitantes esperan una operación compartida |
| Cancelar el cliente podía interrumpir otra vez la limpieza del job | La espera está protegida y la cancelación se solicita una sola vez |
| Fallar al aplicar permisos dejaba un listener creado | Se valida antes de aceptar clientes y se revierte el arranque fallido |
| El sello podía preceder al trabajo residual de proveedores/hilos | Se cierran dependencias y se une el executor antes de sellar |
| SIGTERM durante el arranque o el sellado interrumpía el ciclo de vida | El manejador permanece instalado desde la inicialización hasta el fin de la limpieza |
| La app olvidaba el daemon inmediatamente después de SIGTERM | Conserva su referencia y espera el callback de terminación |
| Un hijo que ignora SIGTERM podía sobrevivir a la app | Límite de diez segundos con terminación del hijo concreto |
| El gate arrancaba antes de publicarse el socket/readiness tras instalar | RC5 espera hasta cinco intentos de las mismas comprobaciones autenticadas; conserva todos los requisitos |

## Verificación reproducible

```bash
.venv/bin/python -m pytest tests/test_runtime_lifecycle.py tests/test_ipc_server.py \
    tests/test_jobs.py tests/test_job_tasks.py
./script/test_native.sh
./script/rc3_rc8_gate.sh
```

Las pruebas Swift ejecutan hijos reales para cierre concurrente, rechazo de doble arranque,
SIGTERM ignorado y agotamiento del presupuesto de reinicios. Las pruebas Python verifican clientes
activos e incompletos, cancelación de quien espera el cierre, rollback de permisos y privacidad del
registro de errores. Las tres regresiones iniciales de cierre fallaron antes de la corrección.

El gate RC requiere código comprometido y app instalada en la misma revisión. Sus mediciones de
memoria/latencia y recuperación no implican que toda aplicación ni toda orden posible esté
certificada. El límite nativo de diez segundos es una salvaguarda ante bloqueo: un cierre forzado
puede dejar la auditoría pendiente de recuperación, nunca se considera un cierre limpio.
