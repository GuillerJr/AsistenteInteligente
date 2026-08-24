# ADR-0067: Presencia nativa en el notch

- Estado: aceptado
- Fase: 5 — Interfaz visual y UX
- Fecha: 2026-08-21

## Contexto

Jarvis operaba desde Menu Bar y mostraba el HUD solo bajo invocación. En un MacBook con notch faltaba
una presencia ambiental que comunicara el estado voice-first sin abrir una ventana o un chat.

## Decisión

La aplicación crea un único `NSPanel` transparente y no activante alrededor del notch de la pantalla
integrada. La geometría se obtiene de `safeAreaInsets`, `auxiliaryTopLeftArea` y
`auxiliaryTopRightArea`; no se codifica una resolución. El panel se reposiciona cuando cambia la
configuración de pantallas y desaparece si no existe un notch real.

La vista SwiftUI muestra indicadores laterales y una línea inferior. Lee `voiceState`, amplitud,
daemon e integridad desde el `MenuBarModel` existente. Pulsar un ala expande el panel y expone solo
un núcleo neural reactivo, `Hablar`, `HUD`, integridad, identidad local y colapso. Las acciones llaman
a los controladores existentes y lo contraen. Voz y estados activos modifican escala, color y brillo;
el reposo es estático. No usa un monitor global, no activa la aplicación, no se restaura, no crea Dock
y no reemplaza el icono de Menu Bar. El HUD 3D conserva su invocación explícita.

La acción de voz es contextual: inicia la captura si está lista, solicita los permisos aún no
determinados o abre el panel de Privacidad correspondiente si macOS los bloqueó. Los diálogos TCC
siguen requiriendo una pulsación explícita y nunca aparecen durante el arranque.

Cuando existe una confirmación de herramienta pendiente, esa condición tiene prioridad y la acción
principal abre la ventana singleton de revisión. El resumen, el vencimiento y las decisiones siguen
fuera del notch para impedir aprobaciones accidentales sin contexto.

## Filtro del algoritmo de ingeniería

1. Se cuestionó convertir el notch en una segunda aplicación completa.
2. Se eliminaron chat, texto editable, preferencias, monitor global y sondeo adicional.
3. SwiftUI renderiza; AppKit solo controla la ventana y la geometría no expuestas por escenas.
4. Un panel y dos primitivas visuales reutilizan estado existente.
5. El panel nace con el daemon visual y se adapta automáticamente a cambios de pantalla.

## Consecuencias

Jarvis permanece visible e interactivo de forma discreta en hardware con notch y conserva el
comportamiento menu-bar-only en los demás equipos. El área física recortada sigue siendo inaccesible;
la interacción ocurre en las alas visibles y la expansión se dibuja bajo su borde.
