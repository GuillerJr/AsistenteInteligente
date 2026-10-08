# Estabilización de skills y extensiones

Fecha inicial: 2026-10-07. Actualización: 2026-10-08.

## Cambios

Configuración incorpora gestión visual de skills y plugins: revisión completa, importación
desactivada, selección explícita de herramientas y recursos declarativos, autorización para NVIDIA
por huella, revocación, desactivación y retirada con copia privada. Los cambios de contenido
invalidan autorizaciones; los archivos editados en sitio se detectan sin reiniciar. Una política
corrupta o llena bloquea la importación antes de escribir o archivar el archivo aprendido.

La selección normaliza acentos, combina hasta tres procedimientos del mismo rol dentro del
presupuesto de contenido y mantiene la última tarea del chat durante seguimientos. Para consultas
inciertas existe un selector NVIDIA acotado; sus respuestas no conceden autoridad. Los
procedimientos sobreviven a la regeneración de respuestas, sin duplicar el cuerpo en los prompts.
La voz sin identidad privada verificada solo ve procedimientos públicos integrados.

Desactivar o retirar un plugin bloquea sus handlers inmediatamente y revalida disponibilidad entre
preflight, DNS, TLS y el envío. La revocación impide nuevos POST detectados antes del envío; no puede
deshacer una petición ya transmitida. Activar o actualizar reconstruye el catálogo al reiniciar.

## Flujos offline

`python -m aegis_core.skill_flow_benchmark` completa 39/39 flujos con HTTP NVIDIA simulado:
sin skills, integradas, aprendidas y plugins con y sin consentimiento. Cubre evidencia ejecutada,
continuidad, cambio de tema, fuentes contradictorias, presupuestos, propuestas falsificadas,
aprobación exacta, replay, revocación y lectura de código temporal. El reporte distingue cobertura
de calidad real y no publica procedimientos privados.

## Piloto NVIDIA real

El primer intento con los modelos de código configurados no obtuvo respuestas útiles: dos
solicitudes devolvieron indisponibilidad y las siguientes quedaron bloqueadas por el cliente.
El catálogo oficial respondió HTTP 200; los IDs DeepSeek V4 asignados a código no figuraban allí.
Se corrigió ese rol usando Nemotron 3.5 Lightning como principal y Nemotron 3 Super como alternativa,
ambos ya configurados. Las preferencias anteriores se conservaron en una copia privada.

La comparación se ejecutó con asignación temporal de Lightning y sin fallback, sin alterar las
preferencias durante el piloto. Hubo doce solicitudes HTTP 200, dos por tarea, con seis ejecuciones
del grafo. Los archivos eran sintéticos y temporales; no se ejecutó su código ni se consultaron
cuentas, documentos personales o servicios externos de herramientas.

| Tarea | Sin skill | Con skill | Resultado del criterio exacto |
| --- | ---: | ---: | --- |
| Acceso a lista vacía | 8.457 s | 11.114 s | Ambas pasan |
| Inventario 18 − 7 | 6.598 s | 2.452 s | Ambas pasan |
| Corrección 38 → 41 | 2.866 s | 2.421 s | Ambas fallan en la cita de línea |

Las seis lecturas quedaron verificadas por el ejecutor y el broker; las skills se seleccionaron
solo en sus variantes correspondientes. Cuatro respuestas cumplieron el JSON, los valores y la
cita exacta. En las dos restantes los valores fueron correctos, pero la cita no coincidió con el
criterio requerido. Una sola muestra por variante y estas tareas pequeñas no demuestran una
mejora general de precisión ni una reducción fiable de latencia. No se alteró el oráculo para
convertir esos fallos en éxitos.

## Mejora del catálogo y del recorrido — 8 de octubre

Se conservan y refuerzan las cinco skills originales; se añaden Google Workspace, análisis de
documentos con evidencia y diagnóstico del Mac. Son ocho procedimientos integrados, sobre
herramientas existentes. Las instrucciones distinguen consultas, borradores y acciones realizadas,
datos parciales, unidades, correcciones y fuentes. No habilitan ejecución de scripts ni presentan
escritura de documentos o generación multimedia como capacidades disponibles.

La selección prioriza frases explícitas, excluye citas y negaciones, conserva seguimientos corteses
y prioriza procedimientos relevantes y autorizados en el catálogo semántico. «Correos sin leer»
se conserva como filtro; «sin leer mis correos» se reconoce como restricción. Una tarea cancelada
no recupera alcance del historial. La continuidad de documentos conserva únicamente esquemas de
lectura, sin heredar argumentos o autoridad para escrituras.

El procedimiento aprobado vuelve a validarse después de las herramientas y llega a la respuesta
final. Revocarlo o cambiar su contenido retira también análisis que podrían citar recursos de esa
versión. Los archivos JSON limpios conservan sus saltos de línea; si el saneamiento los cambia,
se indica que las líneas recompuestas no son citas de la fuente original. Claves JSON duplicadas
no permiten conservar valores secretos ocultos. Las peticiones compuestas de lectura y análisis
dejan de convertirse en nombres de archivo malformados. Las búsquedas explícitas de Google Drive
dejan de pasar por la búsqueda web pública.

La matriz offline amplía 39 controles a 75 comprobaciones de política y flujo: 18 escenarios nuevos
en dos variantes. El catálogo completa 9/9 tareas sintéticas con skills y 7/9 sin ellas; las dos
restantes prueban continuidad sin alcance validado. Rechazos y respuestas conceptuales se cuentan
como controles de política, no como tareas ejecutadas. Las respuestas HTTP simuladas comprueban
el recorrido, no la calidad del modelo.

El primer piloto adversarial real usó seis ejecuciones y doce HTTP 200. Cinco respuestas tuvieron
lectura verificada, pero ninguna cumplió conjuntamente el JSON, los valores y las citas exigidos;
la sexta agotó 40 segundos. Un diagnóstico privado posterior de dos casos y cuatro HTTP 200
confirmó una lista en lugar del objeto pedido, una línea incorrecta y atribución de la decisión
a un tercero. Los fixtures eran sintéticos; no se consultaron datos personales.

Se identificó una pérdida de especialización: el experto inicial solo proponía la lectura, pero
la respuesta con evidencia siempre utilizaba el sintetizador rápido. El cierre ahora usa los
modelos principal y alternativo configurados para ese experto, con las opciones acotadas existentes
y sin otra ronda de herramientas. Planner, acciones directas y consenso de varios especialistas
conservan su recorrido. El intento de activar razonamiento adicional junto a JSON no fue estable:
registró nueve solicitudes, cuatro respuestas HTTP 200 y cuatro casos sin respuesta evaluable
antes del límite global de 180 segundos. Esa activación se retiró. Lightning conserva el modo
rápido, de acuerdo con la recomendación para respuestas estructuradas de
[la guía oficial de NIM](https://docs.nvidia.com/nim/large-language-models/2.0.10/get-started/advanced/get-started-nemotron-3.5-lightning.html);
el cierre conserva el presupuesto reducido que Jarvis ya aplicaba a Super. Cuando
se pide un objeto JSON, su forma, valores finitos y ausencia de claves duplicadas se comprueban
antes de publicar la respuesta cerrada; esto no certifica los valores de negocio.

Una repetición con las preferencias del dueño registró doce solicitudes y once HTTP 200. Sus
cinco respuestas evaluadas no cumplieron todos los criterios exactos; la sexta quedó sin evaluar
al alcanzar el límite de solicitudes. La tabla con la skill obtuvo el cálculo y las citas
correctos, pero numeró mal filas descartadas. Código y atribución conservaron fallos en las
variantes evaluadas. Esta comparación incluye modelos diferentes al cambiar el rol: no mide
aisladamente el efecto del texto de una skill.

Las referencias remotas añaden ahora `source_lines` derivadas del texto ya saneado, con números
originales de líneas vacías y cabeceras. Se conserva `output`; la vista adicional solo se genera
para lecturas exitosas con ruta y fragmentos de hasta 8192 bytes y 128 líneas, dentro de 12 KiB
adicionales. Una disposición modificada, datos inválidos o fragmentos mayores impiden generar esa
vista. Las líneas suministradas previamente se descartan y se recalculan, sin interpretar cifras
ni convertir instrucciones del archivo en autoridad.

Se corrigió además una segunda redacción sobre el JSON escapado: podía eliminar contenido que
seguía a una asignación de credencial ya saneada. Cliente y ruta de ingeniería procesan ahora
esos frames por estructura; ingeniería mantiene su redacción adicional de datos personales.
Las regresiones verifican el payload HTTP simulado, la idempotencia y el resultado bruto intacto.

La comprobación final con las preferencias existentes usó tres casos y siete solicitudes,
incluyendo una alternativa por vencimiento de la principal: seis HTTP 200, sin límite global
agotado. Los tres cierres usaron Super; código llegó a la alternativa configurada y documentos
utilizaron el principal de análisis. Las lecturas y los números de línea enviados quedaron
verificados.

| Caso final con skill | Latencia | Criterio exacto |
| --- | ---: | --- |
| Defectos independientes y función protegida | 17.432 s | Pasa: ambos defectos y líneas correctas |
| Revisiones, cancelación y unidades | 6.048 s | Falla: cálculo y citas correctos; añade la cabecera a filas descartadas |
| Corrección del dueño y fuentes ajenas | 3.249 s | Pasa: cifra vigente, anterior, decisión y cita correctas |

Dos de tres oráculos pasan. El tercero conserva su criterio original: esperaba `[2,5,6]` y
obtuvo `[1,2,5,6]`. No se eliminó la cabecera del resultado para aprobar la evaluación. Esta
comprobación final no contiene una nueva variante sin skills: confirma dos resultados exactos,
pero no demuestra superioridad estadística ni una mejora general de precisión o latencia.
Las paráfrasis sin coincidencias léxicas, el contexto incompleto y los errores del modelo siguen
siendo límites materiales; no se promete efectividad universal.

## Verificación de instalación

El empaquetado inicial detectó un exceso en el ejecutable nativo. Se conserva el presupuesto
de 24 MiB por ejecutable y 440 MiB por bundle: el núcleo compartido usa optimización de tamaño
y excluye los proveedores nativos de lenguaje locales que el runtime NVIDIA-only no utiliza.
Se mantienen OCR, audio, activación, identidad y DTO de compatibilidad; el helper de lenguaje
local responde como no disponible. Los experimentos de borrado de tipos de vista y LTO no
aportaron ahorro y se descartaron. El ejecutable UI conserva símbolos indefinidos y referenciados
dinámicamente con `strip -u -r`, el modo documentado para ejecutables enlazados con sus librerías;
los helpers conservan su tratamiento anterior. Se verifican dependencias, símbolos de enlace,
firma y funcionamiento de la interfaz después del empaquetado.

La suite release detectó una especialización incorrecta del compilador al combinar referencias
parciales a `CharacterSet.contains` con `contains(where:)` y `allSatisfy`. El sanitizador de nombres
usa ahora un recorrido explícito; se conservan los oráculos originales y se añaden controles
Unicode y fronteras de 160 bytes. Se valida también la suite completa con optimización release.

La validación utiliza Ruff, la suite Python del hook obligatorio, las pruebas nativas y la firma
estricta del bundle. El chequeo de IPC se limita a revisión, integridad, disponibilidad de
credencial, conexión Telegram y contrato del catálogo; no lee contenido de Google ni envía
mensajes. La certificación física de voz P10/P11 sigue aplazada.

El control completo del 8 de octubre detectó dos pruebas del instalador cuyo manifiesto fijo
caducaba ese mismo día. Esas pruebas usan ahora un instante válido determinista mediante el
verificador real; se mantienen las comprobaciones de firma, caducidad y rechazo de versiones
anteriores. Las 18 pruebas de actualización segura pasan sin cambios en el instalador.
