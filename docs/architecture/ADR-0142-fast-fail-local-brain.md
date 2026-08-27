# ADR-0142: primer fragmento acotado del cerebro local

- Estado: aceptado
- Fecha: 2026-08-27
- Fases: 1 y 3
- Extiende: ADR-0082

## Evidencia

En el Mac objetivo, iniciar el helper firmado y consultar `SystemLanguageModel.isAvailable` tarda
entre 10 y 20 ms. Una inferencia sintética local completa tardó 2,94 s. Mantener otro daemon o una
sesión Foundation Models residente no elimina el trabajo dominante y sí retendría memoria.

## Decisión

1. `jarvis-local-brain` continúa siendo un proceso efímero por solicitud.
2. El cliente exige el primer evento válido en cuatro segundos, dentro del presupuesto total de 20.
   Si no llega, mata el helper y permite el fallback NVIDIA existente.
3. Después del primer evento se conserva el presupuesto total porque una respuesta larga ya está
   produciendo texto útil y voz incremental.
4. Si hubo cualquier fragmento parcial, LangGraph no añade una respuesta remota: falla cerrado para
   no duplicar o contradecir contenido ya presentado.
5. El helper Swift deja de emitir snapshots idénticos consecutivos. El evento `completed` permanece
   obligatorio incluso cuando su contenido coincide con el último snapshot.

## Consecuencias

- Un modelo local bloqueado deja de congelar la conversación durante hasta 20 segundos.
- El proceso sigue liberando memoria al terminar cada turno y no añade servicio, socket ni estado.
- Menos eventos cruzan los pipes y llegan al chunker sin cambiar el texto final.
- El cortacircuito de 30 segundos existente evita nuevos intentos locales tras el fast-fail.
