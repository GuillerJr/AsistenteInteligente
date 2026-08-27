# ADR-0166: control visual ligado al display de la ventana enfocada

- Estado: aceptado
- Fases: 3, 4 y 5

## Evidencia

Captura, clic y scroll convertían coordenadas con `CGMainDisplayID()`. Una aplicación frontal en un
monitor externo podía producir una imagen vacía, normalizar controles contra otro rectángulo o
rechazar un desplazamiento válido. Usar el display principal como fallback tampoco demostraba que
la ventana autorizada estuviera allí.

## Decisión

1. Accessibility obtiene la ventana enfocada del PID fijado mediante bundle ID, PID y fecha de
   lanzamiento.
2. Una función pura selecciona, entre un máximo de 16 displays activos, el que intersecte mayor área
   de esa ventana. Geometría inválida, displays duplicados o ausencia de intersección fallan cerrados.
3. Los empates se resuelven por el identificador de display menor para que el resultado no dependa
   del orden entregado por el sistema.
4. ScreenCaptureKit captura ese display incluyendo únicamente el proceso autorizado. OCR,
   Accessibility y las coordenadas normalizadas usan exactamente sus límites. El recorrido AX
   excluye ventanas sin intersección y controles no seguros cuyo centro pertenece a otro display;
   un campo seguro sigue bloqueando de forma conservadora toda la observación.
5. El scroll se ubica en el centro de la intersección visible entre ventana y display, nunca en el
   cursor del usuario.
6. Tras percepción y codificación, el helper vuelve a comprobar que la ventana enfocada permanece en
   el mismo display antes de entregar la observación.
7. Solo se usan APIs públicas ya enlazadas: CoreGraphics, Accessibility y ScreenCaptureKit. No se
   añade framework, monitor, polling, caché ni estado persistente.

## Consecuencia

Jarvis puede observar y actuar sobre una app frontal en monitores externos o en una ventana que
atraviesa displays sin confundir espacios de coordenadas. Un movimiento concurrente, una geometría
ambigua o la desaparición del monitor cancela el paso antes de ejecutar una acción.
