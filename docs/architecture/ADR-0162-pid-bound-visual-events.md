# ADR-0162: eventos visuales ligados al proceso

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Evidencia

Escritura, teclas y rueda validaban aplicación, foco o geometría antes de publicar, pero usaban el
tap HID global de CoreGraphics. Entre la última validación y la distribución, un cambio de aplicación
podía dirigir el evento al nuevo proceso. Validar otra vez reduce esa carrera, pero no cambia el
alcance global del canal.

El SDK activo ofrece la API pública `CGEvent.postToPid`, que entrega un evento al flujo de una sola
aplicación. No requiere framework, daemon, permiso o dependencia nuevos.

## Decisión

1. Cada acción captura el bundle ID y PID de la aplicación frontal una sola vez.
2. La aplicación debe conservar ambos valores antes y después de la acción. Reinicio, terminación o
   cambio de frente falla cerrado.
3. Texto, teclas y rueda usan una fuente `privateState` y se entregan exclusivamente mediante
   `CGEvent.postToPid`; el helper no publica acciones en `.cghidEventTap`.
4. Campo enfocado, ventana, elemento bajo el punto y control pulsable deben pertenecer al PID fijado,
   además de coincidir con el bundle autorizado.
5. El clic continúa usando `AXPress`, pero queda ligado al mismo PID para impedir que un relanzamiento
   con el mismo bundle herede una observación anterior.
6. Se conservan límites de teclas, texto literal, sensibilidad, geometría, progreso y recaptura. No
   existe fallback al flujo global, cursor, portapapeles, shell o API privada.

## Consecuencia

Un cambio de foco entre procesos ya no puede desviar entrada de Jarvis a otra aplicación. La acción
permanece vinculada al proceso observado completo y cualquier pérdida de identidad corta el flujo;
la mejora añade comparaciones locales, no latencia de red ni estado persistente.
