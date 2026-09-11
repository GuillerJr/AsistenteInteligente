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
- El HUD usa títulos de 22 pt y explicaciones de 13 pt; el contenido puede envolver y desplazarse.
  Las opciones de navegador se adaptan al ancho. El botón de cierre tiene área de 28 × 28 pt,
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

## Rediseño visual del HUD

La revisión posterior reemplaza el HUD de partículas multicolor por una superficie grafito,
material nativo y sombra de ventana. El formato normal pasa de 560 × 560 a 460 × 480 pt
(aproximadamente un 30 % menos de superficie). Cabecera, contenido y pie tienen zonas propias;
no hay tarjetas ni mensajes superpuestos a la presencia. El título usa 22 pt, la explicación
13 pt y los controles comparten geometría y tratamiento visual.

La presencia es una malla procedural monocromática Canvas, sin SceneKit ni recursos raster.
Su reloj está acotado a 20 fps y se pausa en estados estáticos o con Reducir movimiento. El azul
hielo identifica actividad ordinaria; ámbar, coral y verde conservan el significado del estado.
Los agentes se muestran por nombre, únicamente si tienen actividad positiva. No se inventan
porcentajes, latencias ni fases de progreso. Los estados y políticas de autorización no cambian.

El mismo contrato de tamaño alimenta el panel, la muestra DEBUG y las pruebas geométricas.
Se conserva el modo compacto de 340 × 430, texto desplazable, contraste reforzado y cierre Esc.

### Comprobaciones del rediseño

- Bundle DEBUG compilado y abierto con el flujo canónico. Revisión visual del panel independiente
  de 460 × 480: título, explicación, agentes, pie y cierre visibles sin superposición.
- En 340 × 430 se reducen presencia y espaciado; los cuatro navegadores quedan en dos columnas
  completas, sin necesitar desplazamiento. Las acciones permanecen deshabilitadas en el fixture.
- Revisión visual de resultado incierto y bloqueo de seguridad con alto contraste y movimiento
  reducido. Se conserva el significado mediante texto e icono, no solo mediante color.
- Seis transiciones comprobadas por Accesibilidad: desconexión, auditoría, aprobación, resultado
  incierto, finalización y bloqueo. Notch y HUD proporcionan el mismo diagnóstico completo.
- Escape cierra el HUD independiente y conserva el estado de procesamiento en la muestra.
- 21 pruebas de proyección y geometría correctas, incluido el nuevo contrato de tamaño compartido.
- Suite nativa completa: 246 pruebas correctas en 12 suites.

Las guías de SwiftUI, refactorización de vistas, ventanas y puente AppKit para macOS guiaron
la separación entre cabecera, mensaje, presencia, acciones y pie; el controlador conserva únicamente
geometría, foco y ciclo de vida. La validación visual usa datos ficticios, no ejecuta tareas reales.

## Evidencia del pulido inicial (anterior al rediseño)

Verificación inicial: 20 pruebas nuevas de proyección/geometría correctas (incluyen casos
parametrizados) y 1498 pruebas Python correctas, 17 opt-in omitidas. La revisión visual del bundle
DEBUG comprobó procesamiento, resultado incierto, alto contraste, panel de 340 × 430, cuatro
navegadores y apertura/cierre del HUD con Esc sin modificar la selección pendiente.

La revisión del segundo bundle confirmó la tarjeta junto al borde inferior y dos columnas de
navegadores en 340 × 430. Seis transiciones verificadas mediante Accesibilidad —desconexión,
auditoría pendiente, aprobación, resultado incierto, finalización y bloqueo— devolvieron el mismo
diagnóstico completo en notch y HUD. Esc cerró el panel independiente conservando la selección;
los cuatro botones operativos permanecieron deshabilitados en el fixture.
