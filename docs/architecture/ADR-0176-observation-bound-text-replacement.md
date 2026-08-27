# ADR-0176: reemplazo de texto ligado a observación

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Evidencia

Enfocar y escribir añade texto, pero corregir un campo existente requería `⌘A` y otra acción. Ese
atajo depende del foco global, necesita una captura intermedia y puede seleccionar contenido ajeno si
la interfaz cambia. Accessibility ya permite reemplazar el valor del campo exacto sin esa secuencia.

## Decisión

1. `replace_text` exige únicamente coordenadas normalizadas, descriptor Accessibility completo y un
   literal imprimible de 1 a 500 caracteres. Python y Swift rechazan campos ausentes o adicionales.
2. Las órdenes exactas `Reemplaza el contenido del campo «campo» por «literal»` y su equivalente en
   inglés seleccionan localmente un único campo editable no sensible. El literal no sale a NVIDIA.
3. El helper firmado vuelve a resolver el elemento dentro del mismo PID y proceso, bloquea
   `AXSecureTextField`, compara el descriptor observado y exige que `AXValue` sea modificable.
4. Inmediatamente antes del efecto revalida contexto visual y contador HID. Asigna el literal en una
   operación, vuelve a comprobar proceso y propietario, y exige leer exactamente el mismo valor.
5. El daemon recaptura la aplicación y solo informa éxito si el descriptor Accessibility o la imagen
   prueban progreso. No se usa `⌘A`, portapapeles, Enter ni el puntero humano.
6. OCR no autoriza la acción. Ambigüedad, percepción truncada, contenido sensible, valor no
   modificable, cambio de ventana o entrada física fallan cerrados y no producen un prefijo parcial.

## Consecuencia

Jarvis puede corregir o rellenar de nuevo un campo en una sola acción local, con menos latencia y una
frontera de seguridad más estrecha que la emulación de teclado global.
