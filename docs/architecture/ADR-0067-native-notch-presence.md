# ADR-0067: Presencia nativa en el notch

- Estado: aceptado
- Fase: 5 — Interfaz visual y UX
- Fecha: 2026-08-24

## Contexto

Jarvis operaba desde Menu Bar y mostraba el HUD solo bajo invocación. En un MacBook con notch faltaba
una presencia ambiental que comunicara el estado voice-first sin abrir una ventana o un chat.

## Decisión

La aplicación crea un único `NSPanel` transparente y no activante alrededor del notch de la pantalla
integrada. La geometría se obtiene de `safeAreaInsets`, `auxiliaryTopLeftArea` y
`auxiliaryTopRightArea`; no se codifica una resolución. El panel se reposiciona cuando cambia la
configuración de pantallas y desaparece si no existe un notch real.

La vista SwiftUI muestra un iris neural, alas y onda. Lee `voiceState`, amplitud, wake word, daemon e
integridad desde el `MenuBarModel` existente. No expone acciones: el panel ignora eventos de ratón,
no puede convertirse en key window y no contiene botones ni callbacks. Menú, permisos, HUD y revisión
de herramientas permanecen exclusivamente en Menu Bar y sus ventanas auxiliares.

La presencia cambia automáticamente entre reposo, escucha, envío, procesamiento, aprobación,
respuesta y alerta. El estado compacto respira a 10 fps; los estados activos usan 30 fps para onda e
iris. Reducir movimiento pausa el timeline. ADR-0069 añade un único monitor global de espera larga,
compartido con el HUD, para reflejar agentes iniciados fuera de la UI sin sondeo periódico.

El mismo `TimelineView` gobierna parpadeo, foco, mirada bidimensional, aura, barrido del puente, alas
y siete micro-nodos orbitales. La energía y velocidad salen del estado y de la amplitud ya observable;
no existe otro reloj, tarea ni fuente de aleatoriedad. Con Reducir movimiento, el iris queda abierto
y centrado, mientras la composición conserva una postura estática legible.

La silueta usa el ancho físico del notch y un frame transparente estable. Todas las coordenadas se
redondean a la escala del display; SwiftUI transforma el contenido internamente y AppKit se limita a
posicionar el único `NSPanel` cuando cambia la configuración de pantallas.

## Filtro del algoritmo de ingeniería

1. Se cuestionó convertir el notch en una segunda aplicación completa.
2. Se eliminaron chat, botones, hover, expansión manual, callbacks, monitor global y sondeo adicional.
3. SwiftUI renderiza; AppKit solo controla la ventana y la geometría no expuestas por escenas.
4. Un panel estable y primitivas paramétricas reutilizan un solo reloj y el estado existente.
5. El panel nace con el daemon visual y se adapta automáticamente a cambios de pantalla.

## Consecuencias

Jarvis permanece visible y reactivo en hardware con notch, pero nunca compite con Menu Bar ni
intercepta el puntero. El área física recortada sigue siendo inaccesible; la presencia vive bajo su
borde y responde automáticamente al ciclo de voz y al estado de seguridad.
