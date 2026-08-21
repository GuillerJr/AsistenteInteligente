# ADR-0040: Reintento explícito de la escucha

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** después de agotar la recuperación automática, un botón que solo dice
   `Desactivar` oculta cómo restaurar el micrófono.
2. **Eliminar:** no se añade ventana, alerta, preferencias ni estado nuevo.
3. **Simplificar:** el estado `failed` selecciona dos acciones explícitas en el menú existente.
4. **Acelerar:** reintentar reutiliza exactamente la activación que ya valida modelo y permisos.
5. **Automatizar:** la misma llamada cancela tareas antiguas y restablece el cupo acotado.

## Decisión

Cuando el modelo está disponible, el usuario mantiene una única acción de activar o desactivar en
los estados normales. Si la escucha está en `failed` y el opt-in continúa activo, Jarvis muestra:

- `Reintentar escucha “Jarvis”`, que vuelve a ejecutar `setWakeWordListeningEnabled(true)`;
- `Desactivar escucha “Jarvis”`, que conserva la salida explícita y cancela recuperación.

No se introduce una ruta de recuperación alternativa: ambas acciones atraviesan las mismas
comprobaciones y persistencia local existentes.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** permite recuperar una entrada que sigue fallando.
- **Fase 5 — Voice-first:** mantiene el control en la superficie mínima de Menu Bar.

## Consecuencia

Un fallo persistente permanece cerrado, visible y recuperable con un solo gesto. Jarvis no inicia
reintentos ilimitados ni obliga a alternar dos veces el estado de escucha.
