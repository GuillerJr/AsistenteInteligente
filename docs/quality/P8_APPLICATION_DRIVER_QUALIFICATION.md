# P8 — Calificación real del driver de aplicaciones

P8 valida que Jarvis pueda observar y operar una aplicación macOS sin secuestrar el foco ni el
puntero del propietario. La prueba utiliza el `JarvisComputerHelper` instalado y firmado contra una
aplicación AppKit efímera con controles deterministas. No utiliza mocks en la frontera nativa, no
abre sitios web, no modifica aplicaciones del usuario y no emplea voz.

## Compuerta

```bash
./script/p8_application_gate.sh
```

Antes de operar la fixture, el gate repite P7 y exige que `Jarvis.app`, el checkout y el snapshot de
readiness pertenezcan al mismo commit. Después compila `jarvis-ui-qualification-fixture`, crea un
bundle firmado dentro de `/private/tmp`, lo abre sin activar su proceso y lo destruye al finalizar.
El gate diario compila esa fuente AppKit directamente con el `swiftc` y SDK fijados por el proyecto;
así no resuelve ONNX ni el resto del grafo SwiftPM solo para crear una ventana de prueba. La suite
nativa integral sí compila el target SwiftPM para detectar incompatibilidades de paquete.

## Los siete contratos

| Contrato | Evidencia real |
|---|---|
| `browser_driver_discovery` | Detecta al menos un navegador compatible mediante metadatos locales. |
| `runtime_permissions` | Screen Recording y Accesibilidad están autorizados para el helper firmado. |
| `background_activation` | La fixture se inicia sin reemplazar la aplicación frontal del usuario. |
| `bounded_window_capture` | ScreenCaptureKit captura solo la ventana; JPEG ≤32 KiB y AX localiza controles. |
| `ax_text_replacement` | Cambia un campo mediante `AXValue` y una captura posterior verifica el valor. |
| `ax_press_verification` | Ejecuta `AXPress`, confirma cambio de estado y no usa el fallback de cursor. |
| `focus_and_pointer_isolation` | La aplicación frontal y las coordenadas del puntero físico permanecen iguales. |

Las capturas se decodifican únicamente para medir su tamaño y permanecen en memoria. El reporte
contiene booleanos, latencias y contadores; no serializa la imagen, el árbol AX, títulos de ventanas,
texto de controles, bundle IDs del usuario ni coordenadas del puntero.

La orden de prueba viaja por IPC al daemon y de ahí por `RelayedComputerBridge` al Menu Bar. El Menu
Bar es quien inicia el helper firmado, conservando la identidad responsable a la que macOS concedió
TCC. Ejecutar el binario helper directamente desde Python produciría un falso negativo de
Accesibilidad porque macOS atribuiría la solicitud al proceso padre equivocado.

## Corrección arquitectónica

El helper anterior enviaba eventos al PID correcto, pero localizaba el elemento inicial mediante el
árbol global de la pantalla. Una ventana superpuesta podía interceptar esa coordenada. P8 sustituye
esa resolución por una caminata AX acotada dentro del proceso objetivo: máximo 256 elementos, ocho
niveles, 24 hijos por nodo y 250 ms. También consulta el elemento enfocado directamente en la
aplicación objetivo y acepta una única ventana no frontal como fallback seguro.

El relay interactivo permanece disponible en Modo de Bajo Consumo. Los trabajos especulativos y
esperas no esenciales continúan suspendidos; bloquear `computer.wait` impedía acciones explícitas y
hacía que el Menu Bar reconectara repetidamente, con un coste energético mayor que el long-poll.

## Límite honesto

La fixture demuestra que la infraestructura AppKit/AX/ScreenCaptureKit funciona realmente en este
Mac. No certifica automáticamente cada versión de Chrome, Safari, Arc, Firefox, Xcode o Slack. Cada
driver externo debe añadir después un contrato no destructivo específico por versión. Chrome CDP,
por ejemplo, continúa requiriendo el puerto local 9222 y falla cerrado si no está habilitado.

La precisión biométrica y el wake word físico permanecen diferidos hasta el bloque final de voz.
