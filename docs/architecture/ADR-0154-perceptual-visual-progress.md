# ADR-0154: progreso visual perceptual y semántico

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

Comparar JPEG completos convertía cualquier diferencia binaria en progreso. Compresión, animaciones
pequeñas, confianza OCR o desplazamientos Accessibility de un píxel podían permitir otra acción aun
cuando la interfaz semántica siguiera estancada.

## Decisión

1. `AegisAudioCore` reduce cada `CGImage` a 17×16 luminancias y genera 256 comparaciones horizontales
   (`dHash`) antes de codificar el JPEG.
2. El helper entrega exactamente 64 caracteres hexadecimales; el contrato Python los exige y el
   relay autenticado los conserva sin persistirlos ni enviarlos a NVIDIA.
3. Python considera progreso visual únicamente desde 8 bits de distancia Hamming.
4. En paralelo calcula SHA-256 sobre ventanas ordenadas y elementos Accessibility ordenados con
   rol, texto, capacidad de pulsación y sensibilidad. Omite coordenadas, OCR, confianza y truncamiento.
5. Un cambio semántico basta aunque dHash permanezca estable; ruido visual menor no basta si la
   semántica tampoco cambió.

## Consecuencia

La autoevaluación deja de reaccionar a bytes y mide estructura visual más significado accesible. El
costo es 272 bytes de luminancia, 32 bytes de firma y operaciones constantes locales; no incorpora
Pillow, OpenCV, modelo, archivo, permiso ni llamada de red.
