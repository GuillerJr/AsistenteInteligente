# Interfaz de Jarvis: referencia estática de Codex

Revisión del 22 de octubre de 2026. Este bloque estudia navegación, comandos y
composición para orientar una interfaz propia de Jarvis. Continúa la
[revisión de módulos centrales](CODEX_REA_CORE_CONNECTIONS.md), con fuentes y
declaraciones distintas de las seleccionadas en aquel bloque.

## Artefacto y cobertura

El artefacto es Codex `26.1016.51333`, instalado como
`/Applications/ChatGPT.app`. Se volvió a verificar el SHA-256 de su ASAR:
`9d0260dd37f9c45546d5a89d9d782b8362c9957b70b992e2c3f91df60a046b07`.

REA 6.7.0 procesó **9 módulos completos y 10 declaraciones de nivel superior
extraídas exactamente**, con 211.669 bytes y 73.496 nodos AST. Las declaraciones
conservan hash del archivo original y rangos UTF-8/UTF-16; no se añadieron
envoltorios ni se ejecutó código de la aplicación. Cada ejecución tuvo 768 MiB,
45 segundos, 40.000 nodos semánticos y 20.000 incógnitas como presupuestos.

Las 19 ejecuciones completas no tuvieron errores sintácticos, fuentes omitidas,
ni omisiones de nodos o relaciones por recursos. Retienen 47.353 nodos semánticos,
48.016 relaciones y 14.721 incógnitas estáticas. Estas incógnitas no son defectos:
la semántica sigue marcada como desconocida y las dependencias externas a cada
selección no quedan resueltas. El primer intento dentro del sandbox falló al
compilar el helper de Xcode; se conservó aparte y no se cuenta como análisis.

El [manifiesto](CODEX_REA_INTERFACE_COVERAGE.json) registra las 19 identidades,
estadísticas, límites y hashes, verificados nuevamente contra el ASAR. Los
84.825.986 bytes de evidencia completa y las fuentes de referencia permanecen en
`/private/tmp/codex-rea-interface-20261022`, fuera del repositorio. No se leyeron
credenciales, historial personal, datos de cuenta ni ventanas privadas.

## Contratos observados y criterios para Jarvis

| Superficie | Observación estática | Criterio de implementación propia |
| --- | --- | --- |
| Barra lateral | La declaración 22313 de `app-initial` compone secciones de fijados, recientes y proyectos; mantiene claves de selección y estados separados de carga, error y vacío. | Dar una jerarquía estable a accesos, proyectos y chats; mantener el chat activo reconocible. |
| Grupos plegables | La declaración 22283 compone un encabezado plegable de proyectos/conexiones y una lista acotada con expansión. La 21619 permite excluir chats fijados y mostrar actividad no leída en un proyecto plegado. | Permitir plegar grupos sin perder selección; evitar presentar dos veces la misma conversación por su condición de fijada. |
| Mostrar y ocultar navegación | La declaración 13083 vincula etiqueta accesible, estado expandido, control asociado y atajo del botón lateral. | Cambiar nombre y estado accesible al mostrar/ocultar la barra; conservar una forma de volver a abrirla. |
| Comandos | `command-menu-dialog` combina comandos, búsqueda de conversaciones y grupos; muestra atajos, estados de carga y ausencia de resultados. Seleccionar Tab/Mayús-Tab recorre grupos visibles y evita opciones deshabilitadas. | Paleta de acciones de Jarvis con búsqueda, selección por teclado, estados claros y acceso a acciones existentes. |
| Foco de comandos | El menú conserva el elemento y panel de origen; ciertos comandos cierran el diálogo antes de recuperar el foco y ejecutarse. | Abrir/cerrar comandos debe preservar el borrador y permitir continuar escribiendo. |
| Contexto del editor | `composer-project-picker-content` distingue proyecto seleccionado, búsqueda, carga, error y vacío; ofrece retirar el contexto de proyecto. | Mostrar el contexto y modo actuales junto al editor, con cambios explícitos. |
| Texto del editor | Las declaraciones 1477, 1479, 1480 y 1482 de `app-primary` seleccionan texto según modo de plan, objetivo, proyecto, conversación y trabajo en curso. | Usar textos propios en español que expliquen qué va a ocurrir al enviar. |
| Sugerencias iniciales | `home-starter-prompts` pertenece a una superficie clínica; sus tarjetas insertan una propuesta solo si el editor sigue vacío y devuelven el foco, sin enviar automáticamente. | Las sugerencias generales de Jarvis pueden preparar un borrador editable; no deben sobrescribir texto ni iniciar acciones por sí solas. |
| Estado de conversaciones | La declaración 20496 recibe estado de ejecución, error, no leído y metadatos de proyecto para la fila. | Separar visualmente selección, actividad y error, evitando convertir todos los estados en el mismo indicador. |

La agrupación cronológica de Jarvis en Hoy/Ayer/Últimos 7 días/Anteriores, los
textos, iconos del sistema, tamaños y colores son decisiones propias. No se
atribuyen a esta evidencia de Codex. Tampoco se copian componentes, CSS, imágenes,
marcas o código del artefacto.

## Evidencias principales

- Barra lateral general, declaración 22313:
  `ev_0ec15c96cc4e0da44e71ebdee3d4e53c15183e86b6670fb2bcde43dbba30e9c3`.
- Proyectos plegables, declaración 22283:
  `ev_01d4b4255697440790590c8aeba7ae7df3a84414c30f4954dafcdac78499af04`.
- Lista de proyecto, declaración 21619:
  `ev_7f900c5ac9288a980de36b386bac374fd156b6e8cdbfc55a11008c02d69e5c44`.
- Botón de navegación accesible, declaración 13083:
  `ev_2e6ec22b788e48315af8949c69bb4ee46238904f26a2ef25db06d4961053745c`.
- Menú de comandos completo:
  `ev_654a4851b433f528a4e4a4e9c4fb0a0aa79e232c07964444a539ec5f8deca7b7`.
- Selector de proyecto del editor:
  `ev_06f20e07f54d44d2f69743569f966d7cd8a6a836be724dd1c893c6b830cc722b`.
- Texto contextual, declaración 1477:
  `ev_dee830717cdbf0629bd97ca079793834c7079f65c409d5afba96e29b78a38817`.
- Tarjetas iniciales de la superficie clínica:
  `ev_430f5c6cf77d87c6c98ff013f0410f729fee6c879c23f186da3cf39bda766319`.

## Límites de la comparación

Los nombres de archivo no identifican por sí mismos una función: varios módulos
`sidebar-*` pertenecen a reuniones, tareas programadas o revisión de código;
`composer-placement-state` trata anuncios fijados. Esos nombres no justifican
añadir publicidad ni funcionalidades ajenas a Jarvis. La barra lateral principal
se siguió hasta declaraciones concretas de `app-initial`.

Este bloque observa texto del cliente distribuido. No demuestra cómo se presenta
cada superficie para esta cuenta, la disponibilidad remota, las respuestas del
servidor, ni equivalencia funcional completa. Las pruebas y capturas de Jarvis
deben usar datos sintéticos y comprobar su propia navegación, borrador, atajos,
cancelación y accesibilidad; no sustituyen una observación dinámica de Codex ni el
recorrido manual con VoiceOver pendiente en V1.

## Implementación propia en Jarvis

- Superficies neutras adaptativas, texto del sistema y acento arcilla; cabecera
  compacta con las acciones secundarias en «Más opciones del chat».
- Navegación estable, proyectos y recientes plegables, fechas locales y estados
  explícitos de carga/error de proyectos. Buscar vuelve a desplegar el historial.
- Inicio con cuatro sugerencias editables; no sobrescriben un borrador, no envían
  mensajes y respetan carga, importación y ejecución en curso.
- Panel «Contexto del chat» para proyecto, instrucciones, documentos y actividad.
  Solo un chat nuevo admite cambiar de proyecto; hacerlo conserva sus adjuntos y
  borrador. Un chat existente o temporal no hereda un proyecto anterior.
- Navegador, contexto y chat lateral comparten un único lateral derecho. Las
  ventanas estrechas ocultan la navegación mientras el panel está abierto.
- Editor con Intro/⌘Intro para enviar, Mayús-Intro para salto de línea y soporte
  del sistema de texto para composición IME, selección y deshacer. El foco se
  aplica cuando cambia la petición, y el deshacer se separa por conversación,
  incluso cuando dos borradores tienen el mismo texto.
- Se conservan adjuntos, modo de plan, voz, cola, corrección y cancelación. Los
  atajos pasan por las mismas comprobaciones de disponibilidad que los botones.
- Paleta con contexto, búsqueda e historial de acciones; su selección conserva
  identidad y omite acciones deshabilitadas, sin ejecutar cuando ninguna procede.

No se añadieron servicios de inferencia, autorizaciones automáticas ni nuevas
herramientas del daemon. Las vistas de prueba bloquean las acciones externas;
los cambios locales de presentación se pueden ejercitar con modelos sintéticos.

## Verificación previa al commit

- Suite nativa completa: **591 pruebas aprobadas** (202 XCTest y 389 Swift
  Testing); 34 pruebas nuevas respecto a la revisión anterior.
- Comportamiento cubierto: agrupación y búsqueda, plegado accesible, restauración
  independiente en papelera, contexto del proyecto, sugerencias sin envío,
  presupuesto UTF-8, cola y bloqueos, teclado/IME, foco y aislamiento de deshacer,
  selección de comandos disponibles y sustitución de paneles sin perder borrador.
- 22 capturas sintéticas claro/oscuro en
  `/private/tmp/jarvis-interface-ui`, incluyendo chat con navegador a 800 puntos,
  bienvenida y contexto a 800/1280, búsqueda, pestañas, historial de acciones,
  configuración mínima, menú y aprobación. Se comprobaron nombres accesibles y
  ancho de ventana; se inspeccionaron las composiciones principales y superficies
  compartidas. No se capturaron ventanas del dueño.
- Catálogo español sincronizado desde extracción del compilador: 1.317 entradas.

Los controles de commit/push y la reinstalación firmada se ejecutan después de
esta verificación. El recorrido manual completo con VoiceOver sigue siendo una
validación distinta y no se declara completado con estas pruebas sintéticas.
