# ADR-0167: acciones visuales ligadas a una observación

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Evidencia

El control visual fijaba bundle ID, PID, instancia, display y etiqueta Accessibility, pero cada
captura y acción ejecutaba un helper efímero independiente. Una ventana podía moverse o cambiar de
tamaño después de observarla. Recalcular únicamente el display mantenía coordenadas válidas, pero no
demostraba que pertenecieran a la geometría que produjo la imagen. Además, el retículo de Jarvis se
dibujaba siempre en el display principal.

## Decisión

1. El helper calcula un SHA-256 canónico sobre versión, bundle ID, PID, fecha de lanzamiento,
   identificador y límites del display, y posición y tamaño de la ventana enfocada.
2. La geometría se cuantiza a medio punto para tolerar ruido subpíxel sin aceptar un movimiento de
   interfaz significativo. Valores no finitos, fuera de rango o sin intersección fallan cerrados.
3. La captura compara el contexto antes y después de ScreenCaptureKit, OCR y Accessibility. Solo
   entrega imagen, percepción y digest cuando los tres corresponden a la misma geometría.
4. El daemon valida el digest hexadecimal, lo conserva en `ComputerObservation` y lo copia a la
   siguiente acción. No lo incluye en mensajes del modelo.
5. El decoder Swift exige `expected_visual_context` en clic, escritura, tecla y scroll. El helper
   recalcula el valor antes de cualquier efecto y rechaza una diferencia con el motivo acotado
   `computer_observation_changed`. ADR-0168 define la única recuperación permitida.
6. El helper devuelve el identificador de display únicamente tras una acción correcta. La app usa
   esa respuesta para ubicar el puntero independiente de Jarvis en el `NSScreen` exacto; una respuesta
   fallida o un monitor desconocido no muestran el retículo.
7. El contexto es efímero: no se persiste, registra, audita ni envía a NVIDIA. El canal local HMAC
   existente protege su tránsito entre daemon y app.

## Consecuencia

Una decisión nunca actúa con geometría obsoleta. Jarvis usa la observación original, o la única
observación nueva revalidada por ADR-0168, y su indicador visual aparece en el mismo monitor que el
control validado sin tocar el cursor del usuario.
