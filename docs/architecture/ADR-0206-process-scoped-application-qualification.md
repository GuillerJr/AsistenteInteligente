# ADR-0206: Resolución AX por proceso y calificación AppKit efímera

- Estado: aceptado
- Responsable del requisito: Guillermo / gzambrano27
- Bloque: P8

## Contexto

`CGEvent.postToPid` aislaba el destino del evento, pero la búsqueda por coordenadas comenzaba en el
elemento AX global. Esa combinación no garantizaba operación en segundo plano: una ventana
superpuesta podía ocultar el control legítimo. Las pruebas unitarias demostraban los contratos, no
la cooperación real entre TCC, AX, ScreenCaptureKit, firma y un proceso AppKit vivo.

## Decisión

La resolución espacial recorre exclusivamente las ventanas e hijos AX pertenecientes al PID y a la
instancia de lanzamiento autorizados. Escoge el elemento más profundo y de menor superficie que
contiene la coordenada observada. La búsqueda tiene límites estrictos de tiempo, profundidad, hijos
y elementos. El elemento enfocado se consulta sobre `AXUIElementCreateApplication(pid)`, no sobre el
sistema completo. Si una aplicación no frontal expone exactamente una ventana, esa ventana puede
usarse; múltiples candidatas ambiguas fallan cerrado.

Se añade un gate físico con una fixture AppKit efímera. El gate prueba captura en memoria,
reemplazo AX, `AXPress`, cambio visual, conservación del foco y conservación del puntero. La fixture
se crea en un directorio temporal validado, se firma ad hoc, nunca entra en `Jarvis.app` y se elimina
al terminar.

La cualificación se ejecuta dentro del daemon y utiliza el relay existente hacia el Menu Bar. Así el
helper nace bajo la identidad TCC autorizada de la app, no bajo Python. `computer.wait` se clasifica
como canal interactivo esencial y permanece disponible durante Low Power Mode; el resto de los
trabajos no esenciales conserva su suspensión térmica.

## Consecuencias

- Las ventanas superpuestas dejan de redirigir la búsqueda al proceso equivocado.
- El control AX puede operar una aplicación de fondo sin necesitar el foco global.
- Un cambio aceptado por AX no cuenta como éxito hasta ser observado nuevamente.
- El gate puede repetirse sin modificar correo, navegador, calendario o archivos del usuario.
- P8 produce evidencia del driver base; la compatibilidad por aplicación continúa siendo explícita.
