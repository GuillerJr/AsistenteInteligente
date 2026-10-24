# Pendientes de Jarvis

Decisión del dueño, 24-10-2026: **primero construir la variante completa funcional
de OpenClaw con el nombre Jarvis, mediante investigación con REA y código propio**.
Las mejoras propias se harán después. Se limpió este repositorio; el código del
producto anterior sigue en Git y sus inventarios están congelados.

## Fase A — base completa primero

- [x] Limpiar el árbol de trabajo y conservar puntos de recuperación.
- [x] Conservar inventarios de capacidades del Jarvis anterior para la fase posterior.
- [x] Fijar la referencia de OpenClaw y empezar su análisis REA por bloques.
- [ ] Completar la investigación y los recorridos de la
  [matriz de paridad](rebuild/OPENCLAW_BASELINE_PARITY.md); el censo no es revisión.
- [ ] Cualificar el producto: el recorrido de desarrollo ya acredita app SwiftUI,
  servicio, IPC, sesiones SQLite, inferencia autenticada con Codex oficial,
  cancelación, reinicio y temporales. Falta cualificación de distribución completa.
- [ ] Implementar y verificar las herramientas de la propia base OpenClaw, sesiones
  avanzadas, agentes, memoria base, MCP, skills, extensiones, programador, canales,
  nodos y superficies funcionales incluidas en la matriz. No moverlas a la fase B
  por coincidir con capacidades del Jarvis anterior.
  Avance conectado: autoridad de herramientas, archivos, memoria léxica local y
  gestión MCP stdio desde la app; ver [alcance y límites](rebuild/PHASE_A_TOOLS_MEMORY_MCP.md).
- [ ] Completar aislamiento de ejecución y autorizaciones reales del dueño, sin
  confundir token local con identidad firmada, ni un ACK con cierre de una acción.
- [ ] Distribución nativa autocontenida, actualización, recuperación e instalación
  cualificadas; no reemplazar la app anterior con un bundle de desarrollo.
- [ ] Acreditar la base completa mediante pruebas por capacidad y recorridos reales.
  La fase A sigue abierta hasta cumplir los criterios de la matriz.

## Fase B — después de acreditar A

- [ ] Mejoras propias y herramientas adicionales del
  [inventario backend](rebuild/JARVIS_BACKEND_INVENTORY.md) y
  [nativo](rebuild/JARVIS_NATIVE_INVENTORY.md).
- [ ] Migración reversible y ensayada con datos sintéticos del producto anterior.
- [ ] Repetir las pruebas históricas que correspondan, incluida aprobación,
  revocación y verificación real de resultados. No se declaran resueltas por rehacer.

La conexión elegida sigue siendo Codex con ChatGPT; NVIDIA queda excluido y no hay
respaldo automático entre proveedores. El dueño inicia sesión, escribe contraseñas,
concede TCC y aprueba acciones reales. Los pendientes anteriores permanecen en el
historial del commit `c97ed89`; no se han borrado de los inventarios.
