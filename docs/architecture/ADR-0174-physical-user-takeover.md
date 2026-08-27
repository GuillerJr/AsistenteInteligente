# ADR-0174: cesión inmediata ante entrada física del usuario

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Evidencia

El token visual de ADR-0167 detecta cambios de proceso, ventana y display, pero una persona podía
usar teclado, ratón o trackpad entre la captura y el efecto sin cambiar esa geometría. Recuperar el
contexto como permite ADR-0168 sería incorrecto en este caso: Jarvis competiría con su dueño.

## Decisión

1. El helper obtiene de CoreGraphics el contador acumulado de entradas físicas HID desde el inicio
   del Window Server. No instala listeners, no registra teclas y no conserva el evento.
2. La captura muestrea el contador antes y después de ScreenCaptureKit, Vision y Accessibility. Si
   cambia, descarta la observación y devuelve `computer_user_takeover`.
3. La observación válida transporta su contador solo por el bridge local autenticado. El campo no se
   persiste, no entra al prompt y no se envía a NVIDIA.
4. Cada acción exige el mismo contador. El helper firmado lo comprueba antes del efecto y de cada
   fragmento de escritura; clic, tecla y scroll hacen la comprobación en su frontera final.
5. Los eventos de Jarvis usan una fuente privada dirigida al PID y no mueven el cursor HID. Un
   contador distinto solo puede cancelar, nunca autorizar.
6. `computer_user_takeover` termina toda la sesión como `user_takeover`. No consume un paso, no
   recaptura y queda explícitamente excluido de las recuperaciones de ADR-0168 y ADR-0169.

## Consecuencia

Mover el puntero, hacer clic, scroll o escribir devuelve el control al usuario inmediatamente. La
ruta inactiva no usa CPU adicional y la ruta de acción añade solo lecturas locales de CoreGraphics.
