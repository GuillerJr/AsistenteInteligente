# Cuarta revisión por partes — 22 de octubre de 2026

La [quinta revisión](CODEX_REA_CORE_CONNECTIONS.md) continúa módulos centrales,
dependencias, ciclos de ejecución y fronteras nativas/remotas, y documenta sus
correcciones posteriores en Jarvis.

Continúa [el análisis anterior](CODEX_REA_PARTS.md) y concreta tres mejoras propias
de Jarvis: búsqueda en mensajes, pestañas del navegador e historial de acciones.
El artefacto de referencia sigue siendo Codex `26.1016.51333`, instalado como
`/Applications/ChatGPT.app`. Su ASAR conserva el SHA-256
`9d0260dd37f9c45546d5a89d9d782b8362c9957b70b992e2c3f91df60a046b07`.

## Cobertura y evidencia

REA 6.7.0 analizó **24 módulos completos por separado**, con 266.536 bytes y
81.835 nodos AST. Cada ejecución tuvo 768 MiB de heap, 45 segundos, hasta 40.000
nodos semánticos y 20.000 incógnitas. Las 24 fuentes se analizaron sin errores
sintácticos, fuentes omitidas ni truncamientos por recursos. No hubo envoltorios
ni fragmentos sintéticos en este bloque.

Se verificaron independientemente tamaños y SHA-256 de las fuentes y de los 24
JSON completos de evidencia. Los resultados retienen 53.277 nodos semánticos,
57.569 relaciones y 16.835 incógnitas estáticas. Estas últimas son hechos que el
análisis no resuelve; no constituyen 16.835 defectos. La cobertura semántica sigue
marcada como desconocida/parcial, aun sin omisiones por presupuesto.

El [manifiesto](CODEX_REA_FEATURES_COVERAGE.json) guarda rutas, hashes, rangos
UTF-8/UTF-16, identificadores de evidencia, estadísticas y contratos observados.
Las fuentes y la evidencia completa permanecen en
`/private/tmp/codex-rea-features-20261022`, fuera del repositorio. Se analizó texto
inerte; no se ejecutó código del artefacto ni se leyeron credenciales o historial
personal. Jarvis contiene implementación original, sin código ni recursos copiados.

## Contratos observados y aplicación en Jarvis

| Área inspeccionada | Observación del cliente de referencia | Implementación propia |
| --- | --- | --- |
| Búsqueda y coincidencias | Identidad de conversación/turno, carga de contenido antiguo antes de mostrarlo, cancelación y límite visible. | Búsqueda incremental sobre mensajes cifrados; fragmento y apertura del turno encontrado; consultas obsoletas no sustituyen resultados actuales. |
| Navegador y contexto | Las consultas excluyen pestañas cerradas y retiran suscripciones; distinguen identidad documental de navegación. | Hasta ocho pestañas con navegación independiente, cierre que libera recursos y capturas invalidadas al cambiar de pestaña u ocultar el panel. |
| Consultas y borrado de historial | Disponibilidad explícita; invalidación de datos tras confirmar éxito del borrado. | Historial de herramientas de Jarvis con páginas verificadas, confirmación firmada para borrar y actualización solo después de validar el recibo. |
| Terminales, reanudación y voz | Estados de ejecución separados; identidad comprobada después de esperas; reintentos acotados y limpieza al cancelar. | Criterios de revisión para impedir resultados tardíos y distinguir una herramienta que informa éxito de un efecto externo verificado. |

Los nombres de archivo no bastan para atribuir funciones: `search-state` corresponde
a búsquedas de Pages/Drive, y `browser-dea…` contiene un cliente de catálogo de
documentos. Chronicle abarca actividad del ordenador; la función de Jarvis se
limita a metadatos de las herramientas que él ejecuta. No captura pantalla ni crea
una cronología de la actividad privada del dueño.

## Comportamiento de las tres mejoras

- **Buscar chats** consulta títulos y contenido de mensajes, conserva el cifrado
  del almacenamiento y no crea un índice en texto plano. Respeta los filtros de
  chats activos, archivados y papelera, y excluye conversaciones temporales. Los
  cursores autenticados permiten continuar búsquedas grandes. Un lote incompleto
  muestra «Seguir buscando»; una consulta cancelada no puede reemplazar la actual.
  Abrir una coincidencia antigua carga su ventana de mensajes y permite volver a
  los más recientes antes de continuar escribiendo.
- **Pestañas** conserva por separado dirección, borrador, navegación y carga de
  cada página. Los enlaces del chat reutilizan una pestaña vacía o la URL abierta,
  o crean una nueva. Al alcanzar ocho, avisa antes de abrir otra. Los popups sin
  gesto del usuario se bloquean. Las pestañas se conservan en memoria durante la
  ventana y no se restauran desde disco.
- **Historial de acciones** registra como máximo 1.000 entradas desde esta
  versión, con fecha, herramienta y estado. No guarda argumentos, respuestas,
  títulos, URLs ni nombres de aplicaciones. No importa registros personales
  anteriores y omite chats temporales. Permite filtrar por conversación, eliminar
  una entrada y vaciarlo, con firma vinculada al alcance y revisión. El borrado no
  deshace acciones ni elimina la auditoría técnica de seguridad; la confirmación
  lo explica. Los resultados tardíos no recrean lo borrado, incluso si la escritura
  termina con un error ambiguo después de reemplazar el archivo. Los reintentos
  conservan el número de fallos además del último resultado. Un recibo cuyo
  identificador, herramienta o evidencia no coincide con la autorización se
  registra como fallo controlado, nunca como éxito de una herramienta ajena. La
  mera ausencia de verificación del objetivo externo no se convierte en fallo.

## Verificación de Jarvis

Las regresiones usan conversaciones, archivos, IPC y ventanas sintéticos:

- Búsqueda en turnos antiguos, filtros y espacios de usuario, texto Unicode,
  corrupción del cifrado, cursores manipulados/cíclicos, pausas, cancelación y
  consultas obsoletas. Se comprueba la espera acotada del almacenamiento y su
  índice de metadatos, sin persistir texto buscable.
- Pestañas con historial WebKit real sobre páginas `data:` sin red; selección,
  borradores independientes, títulos dinámicos, atrás/adelante/recarga, límite,
  popups, cierre, liberación y resultados de captura tardíos. Atajos enviados a
  ventanas AppKit sintéticas.
- Historial privado, exclusión de temporales, retención, recuperación tras
  interrupción, reintentos, paginación, borrado durante una ejecución, fallo
  posterior al reemplazo del archivo y aislamiento de fallos de la proyección.
  La prueba del peer gate rechaza clientes no autorizados; los vectores de firma
  Python/Swift verifican método, alcance, revisión, frescura y repetición.
- Vistas pequeñas en claro/oscuro y nombres de botones en el árbol de
  accesibilidad. Esto complementa, pero no sustituye, el recorrido manual con
  VoiceOver pendiente en V1.

Los controles completos son `ruff check src tests`, `pytest`,
`script/test_native.sh`, sincronización del catálogo español y los gates de
pre-push: paquete release, aceptación, flujos de producción y fiabilidad. Las
capturas de las ventanas sintéticas permanecen en
`/private/tmp/jarvis-three-features-final-ui`. El pase nativo completo aprobó 544
pruebas (159 XCTest y 385 Swift Testing), y la comprobación del catálogo confirmó
1.270 claves en español, incluidas las 1.264 extraídas por el compilador.

## Qué sigue sin quedar demostrado

El índice de este bloque contiene 103 importaciones literales y 27 destinos fuera
de las 24 fuentes; algunos ya cuentan con análisis en bloques anteriores. No se
presentan como 27 funciones nuevas ni como dependencias totalmente resueltas.
Quedan relaciones dinámicas, dependencias no seleccionadas, componentes nativos y
comportamiento de servicios remotos que este análisis estático no demuestra.
Analizar todos estos módulos no equivale a haber revisado exhaustivamente toda la
aplicación Codex.

Los cuatro requisitos de V1 que dependen del dueño continúan en
[V1_PENDIENTES.md](../V1_PENDIENTES.md): distribución/notarización, actualización
con esa distribución, recorrido real completo de permisos por aplicación y
verificación con VoiceOver. Las pruebas de estas tres mejoras no cierran esas
casillas.
