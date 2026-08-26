# ADR-0086: resultados de acción verificados

- Estado: aceptado
- Fases: 1, 4 y 5

## Decisión

1. La evaluación terminal distingue entre `succeeded` y `outcome_verified`. El primero indica que
   el flujo terminó de forma controlada; el segundo exige evidencia de que se alcanzó el objetivo.
2. El control visual solo se considera verificado cuando una captura posterior permite al agente
   emitir `done`. Detenerse por acción sensible, estado incierto o límite de pasos produce un
   resultado explicativo, pero `outcome_verified=false`.
3. Las métricas de acciones usan exclusivamente trabajos completados y verificados. Una respuesta
   elegante o un proceso sin error no puede aumentar la tasa de fiabilidad por sí solo.
4. El estado verificado cruza el IPC autenticado y se registra como telemetría pública sin captura,
   objetivo, aplicación, argumentos ni contenido de respuesta.

## Motivo

Una acción autónoma no es correcta porque el ejecutor no haya fallado. La confianza requiere una
postcondición observable. Separar finalización de verificación permite explicar una detención segura
sin declararla falsamente como éxito.

## Límites

- `outcome_verified` no concede permisos ni evita confirmaciones.
- El estado visual sigue siendo no confiable y nunca puede ampliar el objetivo autorizado.
- Las acciones sin evidencia suficiente reducen la métrica; no se reintentan automáticamente.
