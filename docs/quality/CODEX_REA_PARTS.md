# Tercera revisión: análisis por partes — 22 de octubre de 2026

Continuación de [la segunda revisión](CODEX_REA_FOLLOWUP.md), centrada en las dos
fuentes que no completaban REA como archivos enteros. El artefacto es Codex
`26.1016.51333`, instalado en `/Applications/ChatGPT.app`; su ASAR conserva el
SHA-256 `9d0260dd37f9c45546d5a89d9d782b8362c9957b70b992e2c3f91df60a046b07`.

Se analiza texto inerte. No se ejecuta código de Codex ni se consulta el historial
del dueño ni se leen credenciales. Los fragmentos y la evidencia completa quedan
fuera del repositorio; aquí solo se guardan conclusiones y metadatos propios.

## Método y alcance

Los límites sintácticos del AST determinan cada fragmento. Los manifiestos
registran por separado posiciones UTF-16 del analizador y posiciones UTF-8 de los
bytes, hashes, procedencia y cualquier envoltorio añadido para analizar una
declaración aislada. Un índice léxico auxiliar se construye sobre el AST completo
original para conectar declaraciones e importaciones entre partes.

Cada flujo procesa unidades de REA con memoria y tiempo acotados y conserva su
evidencia antes de continuar. Los flujos de navegador y módulo compartido son
independientes y trabajan en paralelo. Un resultado parcial se subdivide; no se
cuenta como terminado porque el proceso haya devuelto código cero. Los intentos
parciales se conservan aparte de las unidades que finalmente cubren sus rangos.

Esto permite completar el recorrido estático de partes que antes agotaban los
recursos. No demuestra equivalencia semántica de los envoltorios, flujo de valores
entre todas las partes, destinos de llamadas dinámicas ni comportamiento real de
los servicios remotos. Los contadores de relaciones no son funciones de producto
ni prueban que una operación pueda ejecutarse.

## Navegador: identidad y cierre de recursos

`browser-page-preload.js` tiene 535.284 bytes y 714 sentencias de nivel superior.
Las 48 partes iniciales se convierten en 55 unidades efectivas: una parte que
agotaba memoria se sustituye por ocho fragmentos menores. Las 55 unidades pasan
REA sin errores sintácticos ni omisiones por presupuesto en sus resultados
finales. El intento parcial también permanece registrado.

Se conservan 121.592 nodos semánticos, 118.276 relaciones y 34.664 incógnitas
estáticas. Estas últimas incluyen valores desconocidos, destinos ambiguos y
llamadas o propiedades dinámicas; no se interpretan como defectos del producto.
Los conteos proceden de entradas derivadas e incluyen elementos sintéticos de
los envoltorios; no son un grafo depurado equivalente al del programa original.

Los rangos copiados abarcan 535.222 bytes originales. Los otros 62 bytes están
identificados: 13 bytes de delimitadores de declaraciones representados mediante
envoltorios y 49 bytes de comentario de mapa de fuentes. La verificación de
procedencia distingue esos cambios del contenido de los cuerpos analizados.
El índice sobre el original conecta 4.595 referencias entre partes base.

Los contratos observados en el texto incluyen una solicitud pendiente de permiso
de anotación, respuesta ligada a su identificador, denegación al descargar la
página, retirada de suscriptores y comprobación de destrucción antes de un montaje
diferido. Son criterios de revisión para Jarvis, no pruebas de esas operaciones
en ejecución ni implementaciones que se hayan trasladado.

## Módulo compartido: contratos y referencias entre partes

`app-shared-60f4bb85435f.js` tiene 5.899.322 bytes y 9.231 sentencias de nivel
superior. Las 284 partes base reconstruyen exactamente el original. El análisis
efectivo combina 61 partes exactas independientes, 50 uniones exactas de partes
contiguas y 24 fragmentos derivados que sustituyen una parte demasiado grande:
**135 entradas analizadas**, sin errores sintácticos, fuentes omitidas ni
truncamientos por recursos en esos resultados finales.

Las entradas conservan exactamente 5.899.310 bytes originales. Los otros 12 bytes
son delimitadores y espacios representados mediante los envoltorios documentados.
Las comprobaciones independientes verifican rangos, hashes y ausencia de huecos
adicionales. Los 135 JSON completos de evidencia también se verifican por tamaño
y SHA-256; los intentos parciales sustituidos no cuentan como cobertura efectiva.

Se retienen 628.268 nodos semánticos, 720.595 relaciones y 190.103 registros de
incógnitas. Son conteos de las entradas derivadas, con su contexto sintético, y
no defectos únicos. El índice sobre el AST original conecta 28.309 referencias
entre unidades, 5.651 exportaciones y 1.643 importaciones de las 46 fuentes de la
revisión anterior. Este índice reconecta nombres, sin resolver valores dinámicos
ni demostrar equivalencia de ejecución entre fragmentos.

Los ocho contratos observados aportan criterios concretos de revisión:

| Área | Criterio observado en el cliente |
| --- | --- |
| Permisos | Cruzar las opciones de aprobación y sandbox con las restricciones disponibles. |
| Inicio | Cancelar esperas y retirar los listeners al disponer del servicio. |
| Respuestas en flujo | Asociar solicitudes a su identidad, acotar esperas iniciales e inactivas y limpiar recursos. |
| Ajustes | Reconciliar cambios optimistas y descartar respuestas de suscripciones cerradas. |
| Preferencias | Distinguir campos visibles, editables y decisiones reservadas al dueño. |
| Paginación | Detectar cualquier cursor ya visto, también ciclos de varias páginas. |
| Reordenación | Verificar pertenencia al conjunto visible y ausencia de duplicados. |
| Programación | Validar tipo de horario, intervalos, horas y días sin duplicados. |

Las ubicaciones, consumidores y evidencias de cada observación están en el
[registro de cobertura](CODEX_REA_PARTS_COVERAGE.json). No se atribuye a estos
contratos de cliente una aplicación efectiva de políticas en el servidor.

## Dependencias adicionales

Se indexaron las importaciones literales de las 46 fuentes focales anteriores:
390 declaraciones de importación y 208 destinos adicionales respecto de ese
conjunto. Estos destinos no se presentan como 208 funciones faltantes ni se
consideran automáticamente revisados por haber aparecido en el índice.

Diez dependencias nuevas de permisos por app, políticas por origen, herramientas
WebMCP, fuentes de proyectos y automatizaciones se analizaron con REA: 36.285
bytes, 10.564 nodos AST y 329 observaciones; diez fuentes analizadas y cero errores
sintácticos. Evidencia padre:
`ev_01b1292f590c8e28a0e5c9fdcafc7ef8416adeca4b25cd3849794262e08d2790`.
La retención de incógnitas semánticas de esta pasada acotada sí alcanza su límite;
las fuentes no seleccionadas y las relaciones dinámicas siguen abiertas.

La inspección de sus cuerpos identifica cancelación antes y después de búsquedas
serializadas; estados de permiso desconocido distintos de permitido/denegado;
restauración de una configuración anterior condicionada a su versión si falla
la segunda escritura; y consultas de sincronización con espera creciente y plazo
máximo. Estos criterios complementan las comprobaciones de identidad y recibos
que Jarvis ya tiene. No se deduce comportamiento del servidor desde esos clientes.

## Las siete discrepancias de integridad

Una comprobación independiente reproduce las siete diferencias de tamaño y hash
entre ASAR y los componentes nativos macOS desempaquetados. Los otros nueve
archivos desempaquetados coinciden con ASAR. REA vuelve a registrarlas en
`ev_b8f97fcf9e36a85a05509c01bda5f58ae85ae84b3b5323c635839ace0e94adb7`.

La firma estricta profunda de Codex y las siete firmas individuales pasan fuera
del sandbox. Además, los siete hashes actuales coinciden con `hash2` de
`CodeResources`, protegido por la firma de la aplicación. Queda acreditado que
los bytes actuales están sellados aunque difieran de los metadatos ASAR.

Firmar después del empaquetado es una explicación plausible del patrón; faltan
los originales previos a la firma o registros de construcción para demostrar la
causa. No se presenta como vulnerabilidad ni como defecto de Jarvis. El fallo de
`codesign` dentro del sandbox con autoridad no disponible se conserva como dato
del entorno y no sustituye la verificación válida realizada fuera.

## Correcciones propias en Jarvis

El contraste de identidad y cancelación encontró una carrera en «Preguntar a
Jarvis sobre esta página»: tras esperar la lectura web se usaban la conversación,
los adjuntos, la URL y el título actuales. Un cambio de chat o navegación durante
la espera podía adjuntar el texto a otro chat o atribuirlo a otra página.

`JarvisBrowserPageCapture` reserva la solicitud y su destino en el gesto, antes
de crear la tarea asíncrona. Comprueba conversación, revisión del chat, documento
y cancelación antes de aplicar un resultado o error. Navegar, recargar, cerrar el
panel o cambiar de conversación invalida la solicitud y libera el botón. Una
lectura antigua no puede borrar el estado de una nueva. Se conserva lo que el
dueño añada a los adjuntos mientras espera y se validan de nuevo los límites al
terminar. El texto se acota también antes de cruzar el puente JavaScript/Swift.

El criterio de cursores repetidos detectó otra omisión: la paginación de proyectos
rechazaba A → A, pero aceptaba A → B → A. Ahora conserva los cursores de las páginas
aceptadas dentro de cada generación. Un ciclo se rechaza antes de modificar las
filas o el cursor vigente. Actualizar o cambiar de proyecto reinicia ese conjunto;
una respuesta antigua no puede contaminarlo.

Son implementaciones propias sobre los modelos de Jarvis. No se incorporan
fragmentos ni recursos de Codex. La revisión independiente del código y de las
pruebas no encontró hallazgos accionables.

### Verificación

- Pruebas focales: **26/26**, con 17 de captura de página y 9 de paginación
  (tres regresiones nuevas de paginación).
- Fixtures de continuaciones controladas para cambios A → B → A, chats sin ID,
  recarga de la misma URL, cierre/reapertura, error tardío, doble clic y lector
  cancelado que retorna después de una lectura nueva.
- Localización: 1.206 claves, 1.200 extraídas por el compilador, comprobación
  correcta; sin claves nuevas. `git diff --check` correcto.
- Comando focal: `AEGIS_NATIVE_TEST_ROOT=/private/tmp/aegis-rea-parts-browser-tests ./script/test_native.sh --filter 'BrowserPageCaptureTests|ProjectConversationPaginationTests'`.
- Los hooks de commit y push ejecutan después las suites completas de Python,
  Swift, compilación Release, aceptación, producción y fiabilidad. Sus registros
  finales acompañan la entrega; las pruebas focales no sustituyen esos controles.

El [registro de cobertura](CODEX_REA_PARTS_COVERAGE.json) conserva identidades,
conteos, referencias REA y hashes de los manifiestos completos externos. Los
archivos de `/private/tmp` son evidencia local temporal, no artefactos publicados
ni almacenamiento permanente. Ningún cuerpo de código de Codex forma parte del
repositorio.

## Pendientes que conserva esta revisión

La búsqueda por contenido de mensajes, varias pestañas en el panel integrado y
el historial general de acciones siguen siendo ampliaciones de Jarvis. No se
marcan realizadas por haber ampliado el análisis de Codex. Los cuatro puntos
abiertos de [V1_PENDIENTES.md](../V1_PENDIENTES.md) mantienen su estado.

También siguen abiertas la resolución de propiedades y llamadas dinámicas, las
dependencias fuera del conjunto seleccionado, la ejecución real y los servicios
remotos. Completar las particiones no permite declarar revisada exhaustivamente
toda la aplicación.
