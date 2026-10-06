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
