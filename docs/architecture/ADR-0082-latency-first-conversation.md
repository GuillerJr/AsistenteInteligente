# ADR-0082: Conversación prioritaria en latencia

- Estado: reemplazado parcialmente por ADR-0083
- Fecha: 2026-08-26

## Filtro de ingeniería

1. **Cuestionar:** todo turno no necesita un LLM dedicado a routing ni otro a reescritura.
2. **Eliminar:** el camino común elimina router remoto, asesor redundante y síntesis textual extra.
3. **Simplificar:** modalidad y términos acotados eligen localmente un único especialista.
4. **Acelerar:** el job se observa cada 100 ms al inicio y la voz local cubre un TTS remoto lento.
5. **Automatizar:** el servicio vuelve al detector local de activación al cerrar cada turno.

## Decisión

El enrutamiento común es determinista y local. Texto general y acciones de usuario van a `planner`;
código, archivos, terminal, red y seguridad van a `code_security`; imagen va a `vision`, y audio o
video no transcrito va a `omni`. El broker continúa siendo la única autoridad para herramientas y
mantiene confirmación explícita para efectos externos o sensibles.

Cuando existe un único resultado sin llamadas de herramientas, ese resultado se devuelve
directamente y debe ser español natural, breve y apto para voz. Solo se usa `synthesizer` para
combinar resultados de herramientas o múltiples análisis. El presupuesto de salida común queda
acotado a 192 tokens para conversación, 384 para acciones del `planner`, 512 para respuestas técnicas
y 768 para acciones técnicas. Los esquemas de herramientas solo se adjuntan cuando términos
operativos explícitos indican búsqueda, lectura o control; un falso negativo mantiene la solicitud
sin acciones en lugar de ampliar autoridad.

La app consulta jobs cada 100 ms durante los primeros cinco segundos, 250 ms hasta los quince y 500
ms después. NVIDIA Magpie conserva prioridad para voz, pero una espera de 1,8 segundos activa el TTS
nativo. El `conversation_id` persiste, aunque una nueva captura requiere otra activación explícita.

## Consecuencias

El diálogo ordinario pasa de tres inferencias seriales a una. Las acciones conservan autorización,
auditoría y confirmación de un solo uso. No existe captura automática posterior a una respuesta.
