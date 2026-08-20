# ADR-0025: atajo global nativo para push-to-talk

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** una app voice-first no debe exigir abrir el menú para cada turno.
2. **Eliminar:** se descartan listeners de teclado, permisos de Accesibilidad y librerías de atajos.
3. **Simplificar:** Carbon registra una combinación fija y entrega un evento sin contenido.
4. **Acelerar:** el callback reutiliza `startVoiceTurn()` y todas sus validaciones existentes.
5. **Automatizar:** el atajo se registra una vez al arrancar y su fallo no bloquea la aplicación.

## Decisión

`Jarvis` registra `⌃⇧Espacio` con `RegisterEventHotKey`. Un controlador singleton posee las
referencias Carbon y expone a SwiftUI solo `install(action:) -> Bool`. SwiftUI conserva el modelo y
la acción; Carbon no conoce audio, credenciales, jobs ni HUD.

La activación no abre ninguna ventana. Ejecuta el mismo flujo que “Hablar 8 s”, por lo que permisos
TCC, integridad de auditoría, daemon disponible, aprobación pendiente y exclusión de turnos siguen
siendo obligatorios. No existe escucha continua ni captura hasta que el evento explícito se acepta.

No se usa `NSEvent.addGlobalMonitorForEvents`: observaría pulsaciones ajenas y podría requerir
Accesibilidad o Input Monitoring. Si macOS rechaza el registro por conflicto, el menú muestra el
estado no disponible; Jarvis conserva las acciones manuales. Unified Logging registra únicamente
registro, fallo o activación, nunca teclas adicionales, transcript o identificadores.

## Encaje en el roadmap

- **Fase 5:** permite operar por voz en segundo plano sin convertir el HUD en una interfaz permanente.
