# ADR-0175: foco de texto ligado a observación

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Evidencia

La escritura literal local exigía que el usuario enfocara previamente un campo. Pedir a NVIDIA que
eligiera el campo añadía latencia y exposición de una captura incluso cuando Accessibility ya
identificaba de forma inequívoca un campo de texto no sensible.

## Decisión

1. Se admite `focus` como efecto independiente con coordenadas y descriptor Accessibility exactos.
   Python y Swift rechazan campos faltantes, extras o fuera de rango.
2. Solo un `ComboBox`, `SearchField`, `TextArea` o `TextField` local, no sensible, con PID y proceso
   autorizados puede recibir foco. OCR nunca lo autoriza.
3. El helper vuelve a resolver el elemento en el punto observado, recorre solo su jerarquía local,
   compara el descriptor completo y bloquea `AXSecureTextField` y términos sensibles.
4. Justo antes de `AXFocused=true`, vuelve a validar proceso, contador HID y permisos. Después exige
   que el elemento enfocado del sistema sea exactamente el mismo.
5. La percepción expone únicamente el booleano `focused`; no añade texto privado. El controlador
   recaptura y exige progreso semántico antes de ejecutar el literal pronunciado.
6. Las órdenes exactas `Escribe «texto» en el campo «etiqueta»` y su equivalente inglés usan esta
   secuencia sin NVIDIA. Si el campo ya está enfocado, omiten el primer efecto. Ambigüedad,
   percepción truncada o contenido sensible abandonan el fast-path.

## Consecuencia

Jarvis puede elegir y rellenar de forma local un campo inequívoco con una captura entre ambos
efectos. No mueve el cursor humano, no confía en OCR y conserva la cesión inmediata de ADR-0174.
