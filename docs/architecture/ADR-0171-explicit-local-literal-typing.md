# ADR-0171: escritura literal local explícita

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Evidencia

El helper ya escribía texto sin portapapeles, ligado al campo Accessibility enfocado y al proceso
autorizado. Sin embargo, incluso una orden que contenía el literal completo requería una inferencia
visual para devolver el mismo texto, añadiendo latencia y enviando una captura innecesaria.

## Decisión

1. Solo `Escribe «…»`, `Escribir «…»` y `Type "…"`, con delimitadores cerrados, activan el fast-path.
2. El literal conserva su Unicode y puntuación exactos, tiene entre 1 y 500 caracteres imprimibles y
   no puede comenzar o terminar con espacios ocultos por las comillas.
3. Indicadores explícitos de contraseña, PIN, token, secreto, clave API, CVV o número de tarjeta
   bloquean localmente antes de contactar al proveedor.
4. El daemon crea directamente una acción `type`; no adjunta imagen, OCR o texto al prompt y no usa
   portapapeles, memoria persistente o logs.
5. El helper firmado conserva todas las barreras existentes: aplicación frontal, PID, instancia,
   campo enfocado idéntico, rol editable no seguro, fragmentos Unicode y contexto visual vigente.
6. Un cambio de contexto puede usar la única recaptura local de ADR-0168 porque el literal proviene
   directamente de la orden aprobada. La acción y el texto deben permanecer idénticos.
7. ADR-0170 recaptura después de escribir y solo declara éxito con progreso semántico o visual.
   Enter y cualquier envío continúan fuera del contrato.

## Consecuencia

La escritura inequívoca evita una llamada de visión y mantiene el texto dentro del Mac. Las órdenes
ambiguas siguen la ruta remota protegida; el fast-path no amplía teclas, permisos ni capacidad de
enviar formularios.
