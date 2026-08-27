# ADR-0163: observación visual ligada a la instancia

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

El helper comprobaba el bundle frontal, pero ScreenCaptureKit elegía la primera aplicación con ese
bundle y Accessibility repetía esa búsqueda después de capturar. Dos instancias o un relanzamiento
durante la operación podían combinar el JPEG de un proceso con el árbol AX de otro. La exclusión de
otras aplicaciones no resuelve esta ambigüedad dentro del mismo bundle.

`SCRunningApplication` expone `processID` en la API pública del SDK. `NSRunningApplication` aporta
además una fecha de lanzamiento estable para distinguir una eventual reutilización del PID.

## Decisión

1. Captura y acción comparten un `ComputerProcessTarget` inmutable con bundle ID, PID y fecha de
   lanzamiento de la aplicación frontal.
2. ScreenCaptureKit selecciona únicamente el objeto cuyo bundle y `processID` coinciden; no existe
   fallback a otra instancia del mismo bundle.
3. La identidad se revalida después de enumerar contenido, después de capturar y después de producir
   firma, JPEG, OCR y percepción Accessibility.
4. El árbol AX nace del PID fijado y conserva solo ventanas, controles e hijos cuyo propietario sigue
   siendo esa misma generación de proceso.
5. Cambio de frente, terminación, relanzamiento o reutilización de PID falla cerrado sin devolver una
   captura parcial ni intentar otra aplicación.
6. No se expone el PID al modelo, no se persiste la fecha de lanzamiento y no se añade dependencia,
   permiso, captura o llamada remota.

## Consecuencia

El JPEG y su contexto local pertenecen a una sola instancia verificable durante toda la observación.
NVIDIA no puede recibir una imagen y un árbol Accessibility de procesos distintos, y la comprobación
añade solo comparaciones locales al ciclo ya existente.
