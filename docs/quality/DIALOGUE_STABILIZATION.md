# Diálogo y evidencia de estabilidad

La estabilización separa tres evidencias: pruebas deterministas del código, conversaciones
sintéticas con la API real y funcionamiento físico de la app instalada. Aprobar una de ellas
no demuestra las otras. Se mantienen NVIDIA como única inferencia y el broker como autoridad
de ejecución; esta etapa no añade integraciones nuevas.

## Cambios comprobables

- La generación conserva los datos de la petición, incluidos números y ejemplos citados.
  El resumen de intención sirve para seleccionar contexto; no sustituye el texto que debe
  responderse ni se impone como un segundo objetivo al generador.
- Las condiciones de entrega inequívocas se separan del tema. Una petición sobre escritura
  mantiene ese tema. Los límites explícitos de palabras se verifican antes de publicar.
- Una cita de Telegram se presenta en `replied_message`, con prioridad sobre otra lista reciente.
  Su contenido permanece como referencia no confiable, sin conceder permisos.
- Una elección ambigua necesita una pregunta concreta. Un dato desconocido necesita una
  limitación explícita. El contrato distingue esas dos situaciones de una respuesta resuelta.
- Una transformación aplica la operación actual al valor o texto anterior. El antecedente
  es una entrada, no una respuesta que deba copiarse. La regresión reproduce el fallo de
  Telegram que devolvía `42` al pedir duplicar `42`, e incluye operaciones y traducción.
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
lee conversaciones almacenadas y no ejecuta herramientas. Los 22 escenarios se repiten dos veces:
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

El JSON registra revisión Git, hash de entradas de compilación, estabilidad de fuente durante
la ejecución, resultados por caso, latencia p50/p95, solicitudes y tokens reportados. No contiene
respuestas, credenciales ni contenido real del usuario. Una fuente que cambia durante la prueba
impide aprobar el informe. `tracked_source_dirty=true` identifica una prueba de desarrollo;
no se presenta como evidencia de la app instalada.

## Límites y certificación

Un resultado de 44/44 solo cubre estos escenarios y esa ejecución, con esos modelos y ese estado
del proveedor. No demuestra que toda conversación será correcta, que Telegram entrega todos
los mensajes o que voz, audio, ventanas y ahorro funcionan físicamente. Las pruebas deterministas
de cancelación, broker y privacidad se ejecutan aparte mediante el hook del repositorio.

La evidencia física de P10/P11 debe pertenecer a la misma revisión instalada. Una captura, una
prueba sintética, contadores antiguos o una calificación de otra revisión no la sustituyen.
Hasta aprobar esa cadena y generar `dist/Jarvis.pilot.json`, la certificación integral sigue pendiente.
La salida reciente del evaluador puede guardarse como `dist/Jarvis.dialogue-stability.json`;
ese archivo tiene alcance de diálogo sintético y no es un manifiesto de piloto.
