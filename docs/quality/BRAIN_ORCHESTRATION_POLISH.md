# Pulido 2 — Cerebro y orquestación

Base: `60067b3`. Fecha: 2026-09-11. Complementa el pulido del CLI y mantiene la inferencia
`nvidia_only`: DeepSeek V4 Pro, con Flash como alternativa remota del transporte. No descarga
modelos locales, amplía permisos ni habilita escritura/shell.

## Defectos reproducidos y correcciones

Antes de corregir el código, 12 casos nuevos fallaron de forma determinista:

| Defecto | Contrato corregido |
| --- | --- |
| EOF prematuro o `finish_reason=length/content_filter` aceptado como éxito | Un stream con texto requiere terminación `stop` y `[DONE]`; un corte es `incomplete_model_response`. |
| Llamada a herramienta dentro de una respuesta truncada | Se rechaza antes de autorizar; no se reintenta ni se ejecuta la propuesta incompleta. |
| Cancelación mientras se espera el primer fragmento NVIDIA | La petición y su espera se cancelan y drenan antes de terminar el padre. |
| Fallo/cancelación del principal bloqueado por un asesor pendiente | Se propaga la causa original y se cancela/drena el trabajo hermano. |
| Una recuperación de memoria fallida dejaba GraphRAG ejecutándose | Los grupos anidados poseen y drenan sus operaciones. La memoria recuperable conserva su degradación existente. |
| Uso de tokens del primer modelo vacío atribuido al fallback | Cada intento empieza con contenido, motivo de cierre y uso independientes. |

También se verifica que un asesor fallido conserve el análisis principal con aviso de revisión
degradada; los errores privados no aparecen en la respuesta. Las razones de cierre malformadas
producen un error tipado y no una excepción accidental de Python. Se rechaza texto posterior al
cierre del stream. Los contadores de uso descartan booleanos y enteros negativos.

`gather_owned` conserva las excepciones originales, incluida `CancelledError`, sin convertirlas en
un grupo que rompería el catálogo público de fallos. Los asesores contienen sus errores operativos;
el principal es obligatorio. No se introduce un nuevo scheduler ni tareas en segundo plano.

La verificación de terminación es común a los proveedores en el grafo y se repite en la frontera
de persistencia. Un turno incompleto no modifica el historial; el siguiente turno puede reutilizar
la misma conversación. El CLI advierte que el fragmento visible puede estar cortado. No continúa
automáticamente código parcial, no cambia a un modelo local y no declara una comprobación técnica.

## Evidencia reproducible

```bash
.venv/bin/pytest tests/test_nvidia_response_integrity.py tests/test_hybrid_brain.py \
  tests/test_nvidia_client.py tests/test_graph.py tests/test_engineering_nvidia.py \
  tests/test_job_failures.py tests/test_jobs.py
AEGIS_RUN_NVIDIA_ENGINEERING_PROBE=1 .venv/bin/pytest tests/test_engineering_nvidia_live.py -s
```

La segunda orden es opt-in: usa la credencial existente del Llavero y envía exclusivamente
conversaciones y archivos sintéticos. Comprueba proyecto nuevo, referencia «la segunda», lectura
de un error de multiplicación y seguimiento con código Python parseado sin ejecutarlo. Las
pruebas deterministas también verifican privacidad, autorización y ausencia de inferencia local.

Validación real del 2026-09-11: **4/4 escenarios y 7 turnos correctos** con
`deepseek-ai/deepseek-v4-pro-0813`. Proyecto nuevo conservó el objetivo de barbería; «la segunda»
conservó HTML/CSS/JavaScript; la lectura señaló `+` en lugar de `*` sin cambiar el archivo;
el seguimiento produjo un bloque Python válido con una suma. Se parseó el código, no se ejecutó.
En esta muestra, la respuesta completa tardó entre 6,54 y 26,25 segundos y el primer fragmento
entre 1,14 y 7,65 segundos. Son observaciones puntuales, no una garantía de latencia.

El commit y el push conservan los gates del repositorio: Python, Swift, paquete, aceptación,
flujos de producción y fiabilidad prolongada. La instalación usa el paquete con daemon embebido
mediante `script/menu_bar_service.sh install`.

## Alcance y límites

Este cierre endurece el contrato operativo del cerebro. No certifica que cualquier respuesta de
un LLM sea correcta, ni sustituye la revisión o ejecución de pruebas del código propuesto. La
latencia, cuota y disponibilidad de NVIDIA siguen siendo externas. El CLI continúa siendo de
lectura autorizada; su historial técnico y el código leído pueden salir a NVIDIA conforme a la
política explícita. No se transmite memoria personal.

El requisito de terminación SSE sigue el
[contrato documentado por NVIDIA](https://docs.nvidia.com/nim/large-language-models/latest/tutorials.html).
