# Diálogo y evidencia de estabilidad

La estabilización separa tres evidencias: pruebas deterministas del código, conversaciones
sintéticas con la API real y funcionamiento físico de la app instalada. Aprobar una de ellas
no demuestra las otras. Se mantienen NVIDIA como única inferencia y el broker como autoridad
de ejecución; esta etapa no añade integraciones nuevas.

## Cambios comprobables

- La generación conserva los datos de la petición, incluidos números y ejemplos citados.
  El resumen de intención sirve para seleccionar contexto; no sustituye el texto que debe
  responderse ni se impone como un segundo objetivo al generador.
- La instrucción vigente se entrega como el último mensaje real del usuario, después
  del bloque de referencia. Las etiquetas `role` del historial permanecen dentro de
  ese bloque y no sustituyen la cronología de mensajes enviados a la API.
- Las condiciones de entrega inequívocas se separan del tema. Una petición sobre escritura
  mantiene ese tema. Los límites explícitos de palabras se verifican antes de publicar.
- Para un conteo exacto de hasta 64 palabras, NVIDIA puede entregar cada palabra en una
  posición numerada. El renderer exige todas las posiciones, una palabra por valor y
  las une en su orden. No completa huecos, recorta contenido ni escribe texto nuevo.
  Una respuesta en cadena también debe superar el mismo conteo antes de publicarse.
- Una cita de Telegram se presenta en `replied_message`, con prioridad sobre otra lista reciente.
  Su contenido permanece como referencia no confiable, sin conceder permisos.
- Una elección ambigua necesita una pregunta concreta. Un dato desconocido necesita una
  limitación explícita. El contrato distingue esas dos situaciones de una respuesta resuelta.
  Una selección sin ordinal como «me quedo con esa», ante dos alternativas explícitas
  y sin cita o selección reciente del usuario, exige aclaración antes de publicar.
  Este patrón acotado no pretende resolver toda ambigüedad del lenguaje.
- Una transformación aplica la operación actual al valor o texto anterior. El antecedente
  es una entrada, no una respuesta que deba copiarse. La regresión reproduce el fallo de
  Telegram que devolvía `42` al pedir duplicar `42`, e incluye operaciones y traducción.
- La extracción de intención usa los modelos configurados para síntesis. La respuesta
  contextual usa los configurados para razonamiento crítico, con las opciones acotadas
  de ensamblaje de respuesta. Respeta las preferencias del usuario y su respaldo;
  una reparación prefiere un modelo distinto del que produjo el resultado rechazado.
  La elección mejora los casos dirigidos, pero su calidad y latencia deben medirse
  en la batería completa. No se cambia ninguna preferencia ni se añade inferencia local.
- El ensamblaje cerrado con Nemotron 3 Super mantiene razonamiento de bajo esfuerzo
  y un presupuesto de 512 tokens, reducido si el límite total es menor. Los cruces
  voluminosos de al menos tres archivos y las agendas con al menos seis tareas y
  dependencias pueden usar hasta 2048 tokens de razonamiento, manteniendo bajo esfuerzo;
  el respaldo GPT-OSS usa esfuerzo medio en esos casos. Se mantiene el límite global.
  Las llamadas
  normales de razonamiento crítico conservan sus opciones. Apagar el razonamiento
  empeoró casos dirigidos; dejar el presupuesto extendido causó tiempos de espera.
  NVIDIA documenta [bajo esfuerzo](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b/modelcard)
  y [presupuesto de razonamiento](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-super-120b-a12b-infer).
  Este ajuste no elimina demoras o indisponibilidad del proveedor.
- Un fallo transitorio del proveedor admite un solo reintento para todo el diálogo,
  con hasta ocho segundos adicionales dentro del límite global de 40 segundos.
  Conserva la misma petición y no publica borradores. Autenticación, límites de uso,
  errores terminales, cancelación, truncación y propuestas de herramientas se propagan.
  Los respaldos y el reintento se incluyen en las solicitudes HTTP medidas.
- Un seguimiento inmediato inequívoco o una cita conserva su texto real en
  `selected_reference`, además de su procedencia. Seleccionar ese contexto no concede
  permisos, no modifica los datos ni calcula una respuesta en lugar de NVIDIA.
- Un JSON de intención inválido admite una sola reparación de esquema con un modelo
  configurado distinto, sin reenviar el texto inválido. Una respuesta rechazada puede
  repararse con el otro modelo configurado. El máximo es dos intentos de
  extracción, hasta tres respuestas si solo se incumple el conteo de palabras y 40 segundos
  totales; cancelación, herramientas, truncación y autenticación
  no se convierten en reparaciones de esquema ni se ocultan.
- La ruta ordinaria genera desde intención y contexto, sin una primera llamada cuyo borrador
  luego se descartaba. Habitualmente utiliza dos llamadas; reparaciones y respaldos pueden
  aumentar ese número. No se publica una respuesta incompleta o con formato rechazado.
- La auditoría mide solicitudes HTTP, latencia y uso reportado por la API, aislados por inferencia.
  Un consumo no informado por la API sigue siendo desconocido. Estos contadores no son una factura.
- Los helpers de contexto privado se extraen de `graph.py` conservando sus límites y el requisito
  de voz del propietario para acceder a memoria privada. La extracción no autoriza nuevas acciones.

## Batería con NVIDIA

```bash
.venv/bin/python script/dialogue_stability_gate.py --live --repeat 2
```

La ejecución requiere la credencial existente del llavero. Envía solo contexto sintético, no
lee conversaciones almacenadas y no ejecuta herramientas. Los 25 escenarios se repiten dos veces:
formato, último paso, selección ordinal y sus variantes, cita antigua, corrección de un dato,
cambio de tema, cantidades entre comillas, operandos, escritura como tema, elección ambigua,
memoria desconocida, observación histórica, transformaciones de resultados y órdenes maliciosas
dentro de referencias.

El evaluador utiliza hechos esperados, restricciones observables y ejemplos negativos; no toma
la autoevaluación del modelo como prueba de corrección. Un fallo de la API también falla el caso.
Los criterios son acotados: no representan una evaluación semántica completa del lenguaje.
La revisión inicial corrigió un límite artificial de 35 palabras en una petición que solo exigía
una frase, y admitió aclaraciones que nombran las opciones sin decir literalmente «cuál».
Esas correcciones evitan atribuir fallos del evaluador a Jarvis; no son mejoras del modelo.
En la elección ambigua, la palabra «elegiste» dentro de una pregunta no afirma una
elección. El evaluador distingue esa aclaración de «Elegiste React. ¿Quieres continuar?».
«No lo sé» es una respuesta válida al recuerdo desconocido; no exige repetir el nombre
del proyecto que el usuario ya mencionó. Estos ajustes del evaluador no cuentan como
mejoras del modelo.
Una definición no necesita repetir el nombre del objeto si lo describe con sus rasgos;
el caso de bicicleta admite vehículo con ruedas o pedales. El conteo exacto permanece.

El JSON registra revisión Git, hash de entradas de compilación, estabilidad de fuente durante
la ejecución, resultados por caso, latencia p50/p95, solicitudes y tokens reportados. No contiene
respuestas, credenciales ni contenido real del usuario. Una fuente que cambia durante la prueba
impide aprobar el informe. `tracked_source_dirty=true` identifica una prueba de desarrollo;
no se presenta como evidencia de la app instalada.

## Límites y certificación

Un resultado de 50/50 solo cubre estos escenarios y esa ejecución, con esos modelos y ese estado
del proveedor. No demuestra que toda conversación será correcta, que Telegram entrega todos
los mensajes o que voz, audio, ventanas y ahorro funcionan físicamente. Las pruebas deterministas
de cancelación, broker y privacidad se ejecutan aparte mediante el hook del repositorio.

La evidencia física de P10/P11 debe pertenecer a la misma revisión instalada. Una captura, una
prueba sintética, contadores antiguos o una calificación de otra revisión no la sustituyen.
Hasta aprobar esa cadena y generar `dist/Jarvis.pilot.json`, la certificación integral sigue pendiente.
La salida reciente del evaluador puede guardarse como `dist/Jarvis.dialogue-stability.json`;
ese archivo tiene alcance de diálogo sintético y no es un manifiesto de piloto.

## Latencia y pruebas complejas

La medición separa `intent`, `vision` y `answer`, contando tiempo y solicitudes incluso si una
etapa falla o se cancela. Los eventos persistidos usan contadores escalares como
`answer_http_requests`, compatibles con `HashChainAuditLog`; el evaluador los agrupa por etapa.
Una prueba de entrega contextual y otra de cancelación usan ese registro real y verifican
la cadena. El registro sintético permisivo de las primeras pruebas no detectó una incompatibilidad
de métricas anidadas que convertía la respuesta de Telegram en un error al auditarla.
Un seguimiento inmediato de gramática acotada con un antecedente
real puede pasar directamente a la generación validada. Una cita explícita en esa misma
operación excluye respuestas recientes competidoras, conservando el marco de referencia.
No se calculan respuestas localmente ni se omite su validación. Una referencia incompleta,
una condición de palabras ambigua o una revisión de borrador conserva la extracción de intención.

Una tarea que nombra un archivo aportado o consulta datos vigentes con contexto real también
puede omitir el clasificador intermedio si no necesita interpretar un conteo de palabras.
Los archivos independientes conservan sus datos sin arrastrar ejemplos conversacionales
ajenos; una referencia retrospectiva conserva el historial. No se reconstruyen archivos ni
se calculan resultados por código en lugar del modelo. Los patrones son acotados y no prueban
que Jarvis resuelva cualquier referencia. Las llamadas de intención que siguen siendo necesarias
limitan la espera del primer modelo a tres segundos cuando hay respaldo configurado. El tiempo
total puede ser mayor por ese respaldo o por una reparación; no se altera la preferencia del usuario.

El JSON solicitado se entrega directamente como datos, sin competir con una segunda envoltura
interna `fulfilled/reason/response`. Se comprueba JSON válido, tamaño y restricciones de entrega:
una lista explícita exige un array y un objeto explícito exige un objeto. El JSON del usuario
no se fuerza al modo `json_object`, que impedía entregar arrays correctamente.
esas comprobaciones no verifican por sí mismas la verdad de sus valores. Una respuesta
escalar numérica del contrato
ordinario puede renderizarse mecánicamente, excluyendo booleanos y números no finitos. Las
expectativas independientes de la batería comprueban los hechos y cálculos. La corrección del
ejemplo de dependencias exige explícitamente un número sin unidad: antes «13 horas» era una
respuesta válida a una instrucción ambigua y no una prueba de razonamiento fallido.

Los cálculos con imagen separan percepción y razonamiento: el modelo visual extrae valores
y relaciones, y el razonador configurado opera con esa lectura provisional y los documentos
aportados. La lectura nunca se publica como respuesta ni concede permisos y
debe ser un objeto JSON con datos, no una respuesta escalar calculada. La petición de extracción
se envía también como última instrucción real: la orden original de cálculo se reserva para el
razonador. Solo esta fase de percepción puede desactivar el razonamiento de Nano Omni,
según su [modo documentado](https://build.nvidia.com/nvidia/nemotron-3-nano-omni-30b-a3b-reasoning/modelcard),
con 16 segundos totales y hasta ocho para el primer modelo cuando hay respaldo.
Una cita parcial conserva el historial de las otras entidades; solo una transformación
inequívoca aplicada exclusivamente a la cita puede excluir respuestas recientes competidoras.
Una observación
incorrecta sigue pudiendo producir un cálculo incorrecto; las pruebas visuales lo detectan.
La selección vacía de herramientas no amplía el registro a todas las herramientas, y mencionar
un archivo de salida no ofrece una lectura del disco. El broker conserva su autoridad.

La batería ampliada se ejecuta con:

```bash
.venv/bin/python script/dialogue_stability_gate.py --live --suite all --repeat 2
AEGIS_RUN_NVIDIA_CONTEXT_PROBE=1 AEGIS_RUN_NVIDIA_COMPLEX_PROBE=1 \
  .venv/bin/python -m pytest tests/test_conversation_context_live.py tests/test_complex_context_live.py
```

Incluye operaciones compuestas, orden estable, dependencias, contradicciones, correcciones
por entidad, cita antigua contra respuesta reciente, archivos CSV/JSON/PDF reales decodificados,
contenido denso, BOM UTF-16, instrucciones incrustadas, visión y reconciliación de imagen con
archivo. Las expectativas se calculan fuera del prompt y se comprueban por código; no se usa
la calificación que el modelo dé de sí mismo. Las pruebas persistidas agregan una cadena de doce
turnos, historial recortado, archivo actualizado, fork y reconstrucción del coordinador.
Hay 70 escenarios distintos: 25 de la batería base y 45 ampliados. El caso de cuatro archivos
incluye 36 pedidos, cancelaciones, devoluciones y cruces por producto y cliente. Otro caso
optimiza ocho tareas con dependencias y dos trabajadores; su respuesta esperada se comprueba
mediante búsqueda de todos los calendarios factibles en eventos, no por otro modelo.
La revisión del evaluador admite «Good evening» y «Good night» como traducciones válidas
de «Buenas noches», pero rechaza «Good morning», que provenía del contexto equivocado.
El caso de pedidos anidados especifica ahora `qty * unit`: antes «total de líneas» admitía
interpretar cantidad de unidades. Estos cambios aclaran pruebas, no mejoran al modelo.

El evaluador pausa fuera del tiempo medido entre muestras. Ante HTTP 429 se detiene por defecto;
`--rate-limit-wait-seconds 60` permite esperar antes de la siguiente muestra prevista, conservando
el fallo y sin repetir esa muestra. Tres límites consecutivos detienen la ejecución. El informe
distingue muestras previstas, cobertura ejecutada, fallos y latencia de muestras satisfactorias;
una cobertura incompleta nunca aprueba. No contiene cuerpos de error ni cabeceras privadas.

Las pruebas de `image_generation` y `file_export` comprueban la sinceridad de la limitación.
Esas capacidades no se consideran implementadas ni certificadas por responder que faltan.
El programa informa `unsupported_capabilities` por separado. El análisis de una imagen o un
PDF adjunto no demuestra generación de imágenes, exportación a Excel ni entrega de archivos.

Los informes de desarrollo y sus fallos se conservan. Solo la prueba final con fuente
congelada y revisión identificada se usa como evidencia de la versión instalada. Las
comparaciones de latencia se hacen entre los mismos casos de la batería base, separando los
nuevos casos complejos; son ejecuciones con un proveedor remoto sujeto a variación, no un
experimento controlado de capacidad de servidor. P10/P11 y la voz física siguen diferidos.
