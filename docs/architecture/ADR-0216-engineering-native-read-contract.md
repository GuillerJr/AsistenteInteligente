# ADR-0216 — Lectura local verificable y recuperación del CLI

Fecha: 2026-09-10. Estado: implementado; calidad semántica general no certificada.

## Problemas confirmados

- La ruta Apple recibía instrucciones sobre herramientas, pero no devolvía llamadas ejecutables.
- El protocolo nativo dependía de que alguien hubiese consultado disponibilidad previamente.
- Cancelar dejaba el proceso persistente con snapshots del UUID anterior.
- El recorte de contexto saltaba turnos grandes y recuperaba otros antiguos.
- El inventario limitaba archivos, pero no todas las entradas/directorios ni la memoria de `scandir`.
- `/workspace` relativo dependía del CWD del cliente; un path inválido parecía un fallo de IPC.

## Decisión

1. Helper 2.2: evento terminal `tool_call` con una lista de hasta dos rutas relativas. Es una
   propuesta, no un efecto. Python acepta el evento únicamente cuando ofreció la capacidad de
   lectura, crea `ToolCall` y lo entrega al broker. No interpreta comandos de shell ni ejecuta
   JSON redactado en una respuesta visible. Helpers 2.0/2.1 conservan soporte de conversación;
   las lecturas nuevas requieren 2.2.
2. Lecturas de 4 KiB, confinadas a la subcarpeta activa y ejecutadas por el lector existente.
   Resultados con posibles credenciales detienen la síntesis. Los resultados privados nunca
   habilitan fallback remoto.
3. El modo `local_only` nativo tiene una ruta explícita: no usa un puerto HTTP abierto como
   prueba de que el proveedor admite historial estructurado o herramientas.
4. Disponibilidad consultada fuera del event loop; errores transitorios negativos se pueden
   consultar otra vez después de diez segundos. Cancelación mata/recoge el hijo antes de reutilizar
   el cliente. Error de presupuesto es `model_context_limit`, no `swarm_execution_failed`.
5. Historial continuo con abreviación explícita de prefijo/sufijo para turnos grandes. No es una
   memoria ilimitada ni un resumen semántico. El inventario se calcula fuera del event loop con
   techo de 8.192 entradas y 4.096 archivos. Una muestra incompleta no prueba ausencia.

## Alternativa descartada

Se ensayó una clasificación separada de intención y respuesta. Las pruebas reales mostraron
más preguntas redundantes y pérdida de referencias; se retiró antes de entregar. No se añadieron
excepciones por frases ni se rebajaron las aserciones para ocultar esos fallos.

## Validación y límite de producto

Pruebas de transporte, cancelación seguida de nueva consulta, negociación en frío, rutas
maliciosas, secretos en archivos, presupuesto UTF-8, cambios de workspace y regresión general.
La evaluación nativa optativa lee un archivo real mediante el broker y revisa además conversaciones.
Sus checks léxicos no certifican semántica: se revisan las respuestas manualmente.

La variante final evaluada aprobó 10/12 escenarios; continúan abiertos la iniciativa tras
concretar una app y la referencia `web` en una conversación de citas. En una variante descartada,
el modelo sugirió multiplicar correctamente pero justificó el cambio con una afirmación falsa
sobre Python. No se declara el CLI equivalente a un agente de programación autónomo fiable.

No se descargó ni habilitó un modelo adicional. El equipo tiene 16 GiB de RAM unificada;
durante las pruebas se observaron 4,68 GiB disponibles y 1,16 GiB de swap. Son una muestra, no
el presupuesto permanente del usuario ni una medición atribuible exclusivamente a Jarvis.
Cualquier evaluación de otro modelo necesita medir pesos, caché, buffers, pico de RAM y swap,
no confundir el tamaño de la descarga con la memoria de ejecución.

Escritura de parches, ejecución de tests y publicación Git desde Jarvis siguen fuera del perfil
de lectura. La robustez del transporte no permite anunciar capacidades todavía no implementadas.
