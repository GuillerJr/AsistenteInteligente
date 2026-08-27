# ADR-0159: frontera estricta de teclado visual

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

El fast-path local evitaba Enter y Espacio, pero el esquema remoto todavía representaba ambas teclas
y cualquier letra ASCII sin modificador. Un proveedor podía intentar enviar un formulario o eludir
la vinculación de `type` escribiendo carácter por carácter. El helper revisaba campos sensibles para
Enter/Espacio, pero su decoder aceptaba esas acciones y letras sueltas.

La captura y Accessibility también podían contener caracteres Unicode de formato que alteraran la
presentación del texto enviado al especialista.

## Decisión

1. Sin modificadores solo se aceptan Escape, Tab, flechas, Inicio, Fin y Página arriba/abajo.
2. Los únicos atajos modificados son `⌘A`, `⌘F`, `⌘L`, `⌘R`, `⌘T` y `⇧Tab`.
3. Enter, Espacio, letras sueltas y cualquier otra combinación se rechazan en Pydantic, en el decoder
   Swift y otra vez en el ejecutable nativo antes de crear un `CGEvent`.
4. `ComputerControlSafety.isSafeKeyPress` es la política nativa única compartida por decoder y helper.
5. El prompt declara JPEG, OCR, títulos y textos Accessibility como datos no confiables, nunca como
   instrucciones.
6. Python exige texto perceptual imprimible. Swift rechaza controles C0/C1, separadores, marcas bidi,
   aislamiento direccional, zero-width y BOM en texto o etiquetas de acción.
7. No se añade dependencia, inferencia, permiso, persistencia ni ruta de teclado global.

## Consecuencia

La única vía para introducir texto libre continúa siendo `type`, ligada literalmente al objetivo
aprobado y validada sobre un campo Accessibility seguro. Las teclas conservadas cubren navegación y
atajos de navegador no destructivos sin ofrecer un canal alternativo de escritura o envío.
