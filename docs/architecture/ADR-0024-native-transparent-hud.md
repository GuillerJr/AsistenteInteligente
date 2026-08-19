# ADR-0024: HUD 3D transparente nativo

- Estado: aceptado
- Fecha: 2026-08-19

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

## Encaje en el roadmap

- **Fase 5:** entrega la primera esfera de nodos 3D invocable y reactiva al enjambre.
- **Siguiente corte:** conectar amplitud local acotada para modular escala y pulso sin retener PCM.
