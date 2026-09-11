# Menú de Jarvis — continuidad visual con el HUD

Base: `e216826`. Fecha: 2026-09-11.

## Diseño y alcance

- Panel de 380 pt que reutiliza `HUDSurface`, `HUDStyle` y `HUDActionStyle`: grafito,
  azul hielo, bordes discretos y radios continuos. Icono de barra y monograma de onda coherentes.
- Texto en caja natural, estado de 15/12 pt y controles de 11–13 pt. Desaparecen las etiquetas
  técnicas LOCAL/ARM64, las tarjetas multicolor y las barras decorativas de voz.
- Una sola acción principal: revisar solicitud, detener control, iniciar voz o configurar permisos,
  conservando la precedencia y las comprobaciones del modelo existente. Revisar no autoriza.
- Accesos directos Imagen, Pantalla y Abrir HUD. Cuatro navegadores en una cuadrícula de dos columnas.
- Permisos desplegables con etiquetas completas y estado textual; no dependen solo del color.
- Activación por nombre y alertas conservan sus acciones y preferencias. Identidad y actualización
  siguen en la cabecera; salida y atajo quedan en el pie. Nada inicia audio o concede permisos al abrir.
- Cabecera y pie fijos; contenido desplazable, limitado a 560 pt (340 pt en la muestra compacta).
  Sin nuevos temporizadores, animaciones continuas, sondeos o dependencias.

Las skills de SwiftUI para macOS y refactorización de vistas guiaron la separación en composición,
voz, permisos, preferencias y componentes pequeños. El estado sigue perteneciendo a `MenuBarModel`.
Las escenas de aprobación y enrolamiento, el HUD, el notch y el backend no se rediseñan aquí.

## Verificación reproducible

```bash
AEGIS_BUILD_MLX=0 ./script/build_and_run.sh --notch-preview ambient
./script/test_native.sh
```

En la ventana DEBUG, activa `Mostrar menú`; `Permisos pendientes` simula accesos no concedidos.
`Panel compacto` limita el contenido a 340 pt y `Alto contraste` refuerza el marco y la superficie.
La vista usa capacidades ficticias deterministas. Tanto la muestra como el MenuBarExtra real en
modo de prueba deshabilitan acciones operativas y omiten el refresco de permisos. El despliegue
y el desplazamiento siguen disponibles. No modifica permisos, preferencias ni datos persistentes.

Evidencia de esta revisión:

- Compilación y ejecución del bundle DEBUG por el flujo canónico.
- Inspección visual de escucha ambiental, aprobación y selección de cuatro navegadores.
- Panel real de MenuBarExtra con permisos expandidos: textos y controles completos.
- Desplazamiento del contenido compacto hasta las preferencias, con cabecera y salida visibles.
- Los cuatro permisos pendientes se exponen como botones etiquetados en Accesibilidad;
  los permisos listos se exponen como información, sin acciones ficticias.
- Siete transiciones —desconexión, auditoría, procesamiento, resultado incierto, finalización,
  seguridad y navegador— mantienen idéntica descripción de estado en notch y menú.
- Aprobación ofrece `Revisar solicitud`; ninguna acción real se ejecutó durante estas pruebas.
- Suite nativa completa: 246 pruebas correctas en 12 suites.

Incidencia durante la calificación Python: la primera ejecución de
`test_daemon_exposes_bounded_runtime_metrics` leyó un pico de 150863872 bytes y un RSS posterior
de 150880256 bytes (16 KiB más). El código existente mide el pico antes del RSS, en dos lecturas
separadas; el resultado es compatible con crecimiento entre ambas. La prueba aislada pasó.
No se modificaron el servidor de métricas ni su prueba para el rediseño del menú, ni se omitió
la calificación completa de los hooks.

Límites: la muestra no sustituye pruebas físicas de todas las pantallas, versiones de macOS o
sesiones de VoiceOver. La inspección de controles deshabilitados no prueba una captura de voz,
un cambio TCC o una autorización real; sus rutas y compuertas permanecen sin cambios.
