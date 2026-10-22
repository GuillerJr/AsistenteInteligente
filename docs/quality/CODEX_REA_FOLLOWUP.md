# Segunda revisión de herramientas con REA — 22 de octubre de 2026

Continuación de [la revisión inicial](CODEX_REA_TOOL_REVIEW.md). El ASAR de
`/Applications/ChatGPT.app` conserva la versión `26.1016.51333` y el SHA-256
`9d0260dd37f9c45546d5a89d9d782b8362c9957b70b992e2c3f91df60a046b07`.
Se trabajó con texto inerte, sin ejecutar los bundles, consultar credenciales o
historial personal, ni incorporar código o recursos de Codex en Jarvis.

## Cobertura nueva y límites

REA completó el análisis sintáctico de 16 fuentes adicionales: consultas de
sesiones de navegador, explicación/denegación/modelo de permisos, estado de MCP,
instalación de complementos, detalle de habilidades, parámetros de voz, fuentes
de proyectos y conversaciones, estado/selección de archivos y acciones/detalles
de automatizaciones. Leyó 195.067 bytes, visitó 54.006 nodos AST y conservó 1.129
observaciones, sin errores sintácticos ni archivos seleccionados omitidos.

Evidencia: `ev_8ee6600ba88e1f76c69149bb9799ce0eb5ef30110c2514c1f31f076662afc956`.
Las rutas exactas quedan en [el manifiesto](CODEX_REA_COVERAGE.json). Las
relaciones semánticas siguen siendo parciales por dependencias no seleccionadas,
llamadas dinámicas y límites del grafo. Un análisis sintáctico completo de estas
fuentes no acredita ejecución, comportamiento del servidor ni revisión exhaustiva
de toda la aplicación. Los nombres exportados observados incluyen los paneles de
instalación de complementos, detalle de habilidades y fuentes de conversaciones;
no se deduce su comportamiento interno solo a partir de esos nombres.

La nueva pasada aislada de `browser-page-preload.js` aumentó de 2 a 4 GiB el
presupuesto del analizador, manteniendo 300 s por fuente. Agotó el tiempo sin
conservar análisis sintáctico completo. Evidencia parcial:
`ev_28177fff10355673524620586c5b85a8b79addbe50b66d019327523e62208a62`.
La recuperación estructural de fuentes no está disponible en este servidor:
REA solicita un proveedor Wakaru para Linux x64 que no está configurado. No se
interpretó ese fallo como ausencia de código o problemas.

`app-shared` también agotó 4 GiB de memoria; la evidencia es
`ev_b328be87bc40feb6514d59ba44527982ceef2ba308d45bedc5107eee67ee3929`.
Se probaron copias exactas de ambos archivos en un directorio aislado, con 2 GiB y
120 s, para retirar el coste del inventario ASAR. Tampoco completó esa pasada
(`ev_a2fa2ea4280a689d50122b39465cfc17ee268f34ccbacb081c790f661700057f`).

Como alternativa, un recorrido sintáctico propio con `@babel/parser 8.0.4`
analizó íntegramente el texto de ambos archivos sin ejecutarlos: 172.873 y
1.162.195 nodos AST, respectivamente. Observó referencias a identidad de marco,
permisos de anotación, control del navegador, cancelación, liberación de recursos
y políticas de aprobación. Estas referencias son criterios para revisar ciclos
de vida y permisos, no pruebas de que se apliquen correctamente en ejecución.

Se seleccionaron 32 declaraciones superiores por esas referencias, conservando
exactamente sus bytes, rangos y SHA-256 en un manifiesto externo. REA completó su
análisis sintáctico: evidencia
`ev_e69ad53350fa081a9bd8c64fb907f3f3699a6b3c8f2eb79c4512e1c9afb35d61`.
La cobertura completa de ese directorio derivado **no** equivale a cobertura
completa de los dos bundles originales: faltan dependencias entre declaraciones,
resolución dinámica y verificación en ejecución. El manifiesto del repositorio
incluye únicamente metadatos de procedencia, sin código externo.

El bundle acumulado de 29 registros queda en
`/private/tmp/codex-rea-evidence-followup-20261022.json` (1.664.434.211 bytes).
Su contador de incógnitas del registro general no sustituye los límites y las
incógnitas de cada análisis, descritos arriba.

## Mejoras propias de Jarvis

Las superficies revisadas sirvieron como criterios de contraste. Los defectos se
reprodujeron en el código de Jarvis y sus soluciones se diseñaron sobre sus
contratos actuales, sin atribuirlos al comportamiento de Codex.

| Área | Problema comprobado | Cambio y regresión cubierta |
| --- | --- | --- |
| Lectura web | IPv6 perdía corchetes y los parámetros `;` se perdían al enviar la ruta. Una URL malformada podía interrumpir otras fuentes. | Normalización y destino de transporte correctos; errores acotados y aislamiento de resultados inválidos. |
| Límite web | `httpx.Response(content=...)` descomprimía antes de aplicar el límite. | Se solicita `identity` y se rechaza otra codificación antes de leer/descomprimir. El buffer comprueba el límite antes de ampliarse. No se añade soporte de compresión. |
| Proyectos | Un archivo corrupto o ilegible se interpretaba como lista vacía y el siguiente guardado podía sobrescribirlo. | Se preservan bytes y asociaciones; se distingue archivo ausente de almacén inválido. La interfaz conserva el último listado y muestra el error. |
| Adjuntos | Si la versión reciente no cabía, podía seleccionarse una versión anterior más corta del mismo nombre. | La versión más reciente reserva el nombre aunque no quepa. Se rechazan duplicados dentro de un envío y se indica la omisión en la referencia construida. |
| Automatizaciones | Borrar la tarea no cancelaba su ejecución o aprobación pendiente. El lanzamiento manual eludía el límite de concurrencia. | Cancelación coordinada y admisión compartida entre lanzamientos manuales y automáticos; respuesta de inicio real. |
| Entrega | Un transporte fallido se contaba como aviso entregado. | Resultado pendiente persistido para reintentar la entrega sin repetir el trabajo; estados de incertidumbre conservadores ante interrupciones. |
| Objetivos | Reanudar un objetivo con 20 pasos consumidos podía ejecutar el paso 21 y producir un registro inválido. | Se rechaza la reanudación agotada y se comprueba el presupuesto antes de enviar otro trabajo. |

Detener y borrar objetivos también coordinan la cancelación de trabajos y
aprobaciones pendientes. Los guardados atómicos impiden que una finalización tardía
resucite un objetivo borrado o revierta una pausa. Las cancelaciones que aún no
terminan se comunican sin confirmar éxito, dentro del límite IPC.

La interfaz comprueba los recibos de inicio: un rechazo por capacidad, ejecución
ya activa o resultado incierto no muestra «Ejecutando». Si se guardó la tarea pero
no comenzó, mantiene su identificador al corregirla. Un resultado de guardado o
lanzamiento desconocido exige actualizar antes de reenviar, para evitar crear o
ejecutar duplicados. Los fallos transitorios de NVIDIA sin evidencia de que no se
actuó tampoco relanzan automáticamente el trabajo entero.

La cancelación evita pasos posteriores, pero no deshace comandos ya despachados.
La entrega puede duplicar un aviso si el proceso termina después de entregarlo y
antes de guardar el acuse; no se promete entrega exactamente una vez. Los
metadatos de omisión de adjuntos describen los documentos disponibles al construir
la referencia, no un inventario completo de todo el historial.

Las pruebas utilizan páginas, proyectos, documentos, transportes y trabajos
sintéticos. No envían mensajes reales, conceden permisos TCC ni operan sobre
archivos personales. Las certificaciones manuales P1-5/P1-12 y los pendientes de
Developer ID/notarización conservan su estado en V1_PENDIENTES.md.

La cola persistente reserva un presupuesto JSON explícito de 16 MiB. Se prueba
con 32 tareas, resultados y textos máximos con caracteres de cuatro bytes,
recarga y modificaciones posteriores. Los otros consumidores mantienen el límite
por defecto de 256 KiB; el escritor comprueba el tamaño serializado antes de
tocar el archivo existente.

## Verificación reproducible

La suite nativa completa de esta revisión pasó: 384 pruebas del núcleo y 101 de
interfaz (485 en total). Localización: 1.206 claves, 1.200 extraídas por el
compilador y comprobación correcta. Las cifras de Python y de los controles de
publicación se obtienen del registro del commit/pre-push de la revisión final.

- Python y lint: `ruff check src tests` y la suite completa de `pytest` del entorno `.venv`.
- Swift: `script/test_native.sh`, incluidos los recibos de inicio y la recuperación de proyectos.
- Localización: `script/sync_localizations.py --check --build-root /private/tmp/aegis-context-v2-tests`.
- El pre-push ejecuta además compilación Release, aceptación, flujos de producción y fiabilidad prolongada.
- La instalación se verifica por revisión en Info.plist, firma estricta, tamaño del ejecutable, autoprueba VAD y `script/jarvis_beta.sh check`.
