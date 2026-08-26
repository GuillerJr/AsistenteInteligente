# ADR-0117: compuerta de percepción visual local

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Decisión

1. El helper firmado limita cada captura a la aplicación cuyo bundle ID fue aprobado.
2. Antes de un proveedor remoto ejecuta `VNRecognizeTextRequest` y recorre el árbol Accessibility de
   esa aplicación con límites de profundidad, elementos, hijos, ventanas y longitud de texto.
3. El contrato local contiene como máximo 48 observaciones entre Vision y Accessibility. Ventanas,
   roles, etiquetas y centros normalizados se validan otra vez en Python.
4. OCR es percepción no confiable y nunca concede autoridad para pulsar. Solo un elemento
   Accessibility que soporte `AXPress`, tenga coordenadas válidas, no sea sensible y coincida de
   forma única con una orden exacta puede producir una acción local.
5. Campos seguros o términos visuales asociados a credenciales, pagos, envíos, descargas, permisos
   o borrado bloquean la sesión antes de enviar una imagen fuera del Mac.
6. Si la orden no es exacta o el control es ambiguo, el resumen no sensible precede a la captura en
   el contexto del rol visual NVIDIA. Las barreras nativas se vuelven a aplicar al ejecutar.

## Motivo

La mayoría de controles convencionales ya exponen semántica mediante Accessibility. Consultar esa
fuente y Vision local reduce latencia, evita llamadas remotas innecesarias y crea una compuerta de
privacidad antes del fallback multimodal, sin añadir OCR, RPA ni runtime de terceros.

## Límites

- Una coincidencia OCR no se convierte en clic si Accessibility no confirma un control accionable.
- Canvas, juegos y superficies sin accesibilidad conservan el fallback visual.
- Una acción local exacta confirma que `AXPress` terminó, no interpreta el estado semántico posterior.
- Screen Recording y Accessibility siguen siendo permisos explícitos administrados por macOS.
