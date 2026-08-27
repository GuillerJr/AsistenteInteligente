# ADR-0152: prueba local de progreso tras una acción determinista

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

El fast-path evitaba NVIDIA para clics Accessibility, desplazamientos y teclas inequívocas, pero
interpretaba como objetivo completado cualquier retorno exitoso del helper. `AXPress` y CoreGraphics
solo confirman entrega; una aplicación puede aceptar el evento sin modificar su estado visible.

## Decisión

1. Jarvis conserva en memoria la observación que autorizó la acción local.
2. Tras ejecutar y consumir el mismo intervalo acotado de estabilización, recaptura una sola vez.
3. La huella incluye el JPEG filtrado de la aplicación y el árbol Accessibility ya disponibles; no
   crea otro formato ni estado. Barra de menús, cursor, audio y ventanas ajenas quedan excluidos.
4. Si ambas observaciones son idénticas, la sesión termina como `blocked/uncertain_state` después de
   una acción ejecutada. No reintenta ni llama a NVIDIA.
5. Si existe un cambio, la orden determinista de un solo paso se completa como antes.

## Consecuencia

Un éxito de bajo nivel deja de equivaler automáticamente a progreso. El camino añade una captura
local, pero ninguna inferencia, dependencia, persistencia o nueva autoridad; también corta bucles de
clic y scroll sin efecto antes de que puedan repetirse.
