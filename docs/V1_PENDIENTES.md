# Pendientes de la variante propia

El 24-10-2026 el dueño pidió limpiar este repositorio y empezar aquí una variante
propia mejorada, informada por el análisis de OpenClaw. El código anterior se
conserva en Git. Los inventarios históricos de `docs/rebuild/` conservan las
capacidades que se integrarán después.

## Fase A — primero la base independiente

- [x] Limpiar el código antiguo del árbol de trabajo y conservar un punto de recuperación.
- [x] Registrar módulos/herramientas históricas sin tratarlos como funciones de la base nueva.
- [x] Primer núcleo propio: sesión, operación única, autorización exacta, cancelación,
  asentamiento y recibos de efectos inciertos, probados con fixtures.
- [ ] Completar investigación REA por partes. La cobertura global sigue siendo parcial.
- [ ] Añadir límites de tiempo, datos y retención; aislamiento y recuperación productivos.
- [ ] Persistencia, eventos y recuperación de sesiones propias; temporalidad y privacidad.
- [ ] Conexión oficial aislada a Codex con ChatGPT, catálogo y manejo de interrupciones.
- [ ] Transporte local autenticado y supervisión del servicio.
- [ ] App nativa macOS SwiftUI: ventana propia, Dock, barra de menús y atajo.
- [ ] Extensibilidad/MCP genéricos con fixtures y contratos de autoridad, sin conectores Jarvis.
- [ ] Cualificación real de la base y empaquetado propio; la fase A todavía no está terminada.

## Fase B — herramientas Jarvis, posteriormente

- [ ] Recorrer todos los dominios del [inventario backend](rebuild/JARVIS_BACKEND_INVENTORY.md).
- [ ] Recorrer todos los módulos del [inventario nativo](rebuild/JARVIS_NATIVE_INVENTORY.md).
- [ ] Implementar los adaptadores de herramientas, memoria personal, computadora,
  navegador, audio, canales, tareas, dispositivos e interfaz complementaria.
- [ ] Recuperar garantías de aprobación/revocación, cifrado, firmas y migración reversible.
- [ ] Certificar cada capacidad con pruebas reales y datos sintéticos antes de sustituir
  el producto instalado. El dueño concede TCC, introduce contraseñas y aprueba acciones.

El plan detallado está en [docs/rebuild/README.md](rebuild/README.md). Los pendientes
anteriores permanecen en el historial del commit `c97ed89`; no se declaran resueltos
por limpiar el código ni por cambiar de arquitectura.
