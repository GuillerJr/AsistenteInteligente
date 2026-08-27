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
3. El helper produce un dHash de 256 bits desde el `CGImage`; Python combina esa firma con un digest
   de semántica Accessibility. Barra de menús, cursor, audio, OCR y ventanas ajenas quedan excluidos.
4. Progreso exige un cambio semántico o al menos 8 bits de distancia visual. En caso contrario la
   sesión termina como `blocked/uncertain_state` después de una acción ejecutada.
5. No reintenta ni llama a NVIDIA. Si existe progreso suficiente, la orden determinista se completa.

## Consecuencia

Un éxito de bajo nivel deja de equivaler automáticamente a progreso. El camino añade una captura
local, pero ninguna inferencia, dependencia, persistencia o nueva autoridad; también corta bucles de
clic y scroll sin efecto antes de que puedan repetirse.
