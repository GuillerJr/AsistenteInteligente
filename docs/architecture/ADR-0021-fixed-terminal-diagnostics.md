# ADR-0021: diagnósticos fijos de terminal

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** “terminal” no implica exponer un shell a un modelo.
2. **Eliminar:** no hay intérprete, argumentos libres, tuberías, scripts ni nueva dependencia.
3. **Simplificar:** una enumeración selecciona tres comandos nativos e inmutables.
4. **Acelerar:** se reutiliza la aprobación exacta y el ejecutor local existentes.
5. **Automatizar:** el daemon aplica timeout, entorno mínimo, límite de salida y auditoría.

## Decisión

`terminal_run_template` permite únicamente `git_status`, `list_processes` y `list_listeners`. Cada
valor se resuelve internamente a un binario absoluto con argumentos constantes y se ejecuta sin
shell. La llamada es crítica y exige el mismo grant exacto, efímero y de un solo uso del sondeo TCP.

El entorno del subproceso contiene solo rutas de sistema y locale estable. Git deshabilita locks
opcionales, configuración global y de sistema, hooks, fsmonitor y caché de archivos no rastreados.
Cada proceso vence a los tres segundos; stderr se descarta y stdout se limita a 16 KiB antes de
entrar al resultado público del job. Un código inesperado falla sin exponer stderr.

La ventana singleton de aprobación permanece bajo invocación explícita. Para estas plantillas
advierte que la operación es fija y de solo lectura; no muestra ni acepta texto ejecutable.

## Encaje en el roadmap

- **Fase 4:** añade observación local de repositorio, procesos y sockets sin abrir ejecución remota.
- **Fase 5:** reutiliza la superficie voice-first; el HUD posterior podrá representar esta actividad
  sin modificar el contrato de aprobación.
