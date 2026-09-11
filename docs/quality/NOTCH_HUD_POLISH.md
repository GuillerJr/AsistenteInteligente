# Pulido — Notch, HUD y estados

Base: `3cf9eaf`. Fecha: 2026-09-11.

## Contratos

- Una única proyección para las tres superficies: título, color, símbolo y explicación coherentes.
- Seguridad fallida → conexión → auditoría → interrupción/fallo → inferencia → aprobación/selección
  → turno → agentes → finalización/escucha/espera. La proyección no autoriza acciones.
- Un servicio desconectado nunca se presenta como listo aunque queden datos anteriores de voz,
  proveedor o agentes. Cero tareas y contadores negativos no iluminan agentes inexistentes.
- Los errores desconocidos producen mensajes acotados, sin credenciales, rutas ni respuestas.
- «Turno finalizado» describe el final de la respuesta, no el éxito de una acción. Un resultado
  incierto advierte que se comprueben cambios antes de repetirla; cancelar no promete rollback.
- La aprobación pide revisar la solicitud; no invita a pronunciar una autorización a ciegas.
- El HUD usa títulos de 18 pt y explicaciones de 13 pt; el contenido puede envolver y desplazarse.
  Las opciones de navegador se adaptan al ancho. El botón de cierre tiene área de 32 × 32 pt,
  etiqueta accesible y Esc; cerrar no cancela. El control del Mac conserva su acción de parada.
- El HUD se centra en la pantalla del puntero al abrirlo y conserva una posición válida al
  cambiar pantallas. El notch usa coordenadas reales, incluidos orígenes negativos y escala física.
- Reducir movimiento detiene animación continua y transiciones; aumentar contraste/reforzar
  opacidad mantiene el texto sobre negro. Los elementos decorativos no fragmentan Accesibilidad.

## Flujo reproducible

```bash
./script/test_native.sh --filter 'AssistantPresentationTests|AssistantPanelLayoutTests'
AEGIS_BUILD_MLX=0 ./script/build_and_run.sh --notch-preview processing
```

La ventana DEBUG permite elegir estados, forzar movimiento reducido y alto contraste, probar
un HUD de 340 × 430 y abrir el panel independiente. Incluye notch simulado de 192 × 32 para
comparar la misma información sin depender de un recorte físico. No arranca daemon, micrófono,
monitoreo ni autorizaciones; los controles operativos de las muestras están deshabilitados.
No guarda ajustes del fixture ni modifica preferencias de accesibilidad del sistema.

El panel usa el contrato de [NSPanel no activador](https://developer.apple.com/documentation/appkit/nswindow/stylemask-swift.struct/nonactivatingpanel)
y conserva el puente AppKit limitado a geometría, foco y ciclo de vida. El estado sigue en SwiftUI.

## Alcance de la evidencia

Las pruebas geométricas cubren pantallas pequeñas, secundarias con origen negativo, recuperación
de una ventana fuera de pantalla y rechazo de recortes/escalas inválidos. No equivalen a una prueba
física de cada combinación de pantallas, Spaces, VoiceOver o versión de macOS.
La ruta del CLI continúa NVIDIA-only y las funciones locales de voz permanecen separadas.

Verificación inicial: 20 pruebas nuevas de proyección/geometría correctas (incluyen casos
parametrizados) y 1498 pruebas Python correctas, 17 opt-in omitidas. La revisión visual del bundle
DEBUG comprobó procesamiento, resultado incierto, alto contraste, panel de 340 × 430, cuatro
navegadores y apertura/cierre del HUD con Esc sin modificar la selección pendiente.

La revisión del segundo bundle confirmó la tarjeta junto al borde inferior y dos columnas de
navegadores en 340 × 430. Seis transiciones verificadas mediante Accesibilidad —desconexión,
auditoría pendiente, aprobación, resultado incierto, finalización y bloqueo— devolvieron el mismo
diagnóstico completo en notch y HUD. Esc cerró el panel independiente conservando la selección;
los cuatro botones operativos permanecieron deshabilitados en el fixture.
