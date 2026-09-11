# ADR-0024: HUD 3D transparente nativo

- Estado: parcialmente sustituido por ADR-0069 y ADR-0221
- Fecha: 2026-08-19

La arquitectura nativa del panel se conserva. El sondeo fue sustituido por `swarm.wait`
(ADR-0069) y la visualización SceneKit por una presencia monocromática Canvas a 20 fps
en una superficie grafito de 460 × 480 pt (ADR-0221). Las secciones siguientes registran
la decisión original, no el renderizador vigente.

## Filtro de ingeniería

1. **Cuestionar:** Tauri/Electron/Three.js no son requisitos funcionales para una esfera 3D local.
2. **Eliminar:** se descartan runtime web, Node, bundler, WebView y dependencias descargables.
3. **Simplificar:** un `NSPanel` singleton aloja SwiftUI y un `SCNView` adapta el estado del daemon.
4. **Acelerar:** SceneKit renderiza directamente en el bundle arm64 y reutiliza el IPC existente.
5. **Automatizar:** abrir inicia el sondeo; cerrar lo cancela, limpia estado y registra la transición.

## Decisión

La Menu Bar ofrece “Mostrar HUD…” y el script operativo acepta `hud`. Ambos son acciones explícitas;
el arranque normal no abre la ventana. Un `NSPanel` borderless singleton es transparente, de tamaño
fijo, no restaurable, flotante y movible por el fondo. AppKit controla únicamente su ciclo de vida;
un `NSHostingView` conserva la composición declarativa y un botón de cierre visible.

`NodeSphereView` usa SceneKit con cámara explícita, 30 FPS, 210 nodos de baja geometría y rotación
lenta. Una distribución Fibonacci cubre la esfera y asigna cada nodo a la región espacial más
cercana de los siete roles. Cada clúster comparte geometría y material; `swarm.activity` únicamente
modifica emisión e intensidad. SceneKit nunca almacena estado de orquestación.

El HUD consulta actividad cada 250 ms solo mientras está visible. Al cerrarse, la tarea se cancela y
el diccionario visual se vacía. Unified Logging registra únicamente `hud_opened` y `hud_closed`, sin
roles, identificadores ni contenido.

Esta política de consulta fue reemplazada por ADR-0069: un único `swarm.wait` autenticado mantiene
estado efímero para HUD y notch mediante espera larga, sin abrir automáticamente la ventana.

El tap compartido de la transcripción entrega al modelo visual únicamente el campo `activity` ya
normalizado. El callback se ejecuta en la cola coalescida del medidor, nunca en el hilo de audio, y
SceneKit lo convierte en una escala entre 1 y 1,14. El nivel vuelve a cero al finalizar el turno; no
se registra, persiste ni envía por IPC y el callback no recibe muestras PCM.

## Encaje en el roadmap

- **Fase 5:** entrega una esfera 3D invocable, reactiva al enjambre y pulsante con la voz local.
