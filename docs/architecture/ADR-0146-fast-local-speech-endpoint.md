# ADR-0146: cierre rápido de frase local

- Estado: aceptado
- Fases: 1, 3 y 5

## Evidencia

La captura on-device esperaba 1,2 segundos continuos de silencio antes de cerrar una frase. Después
finalizaba Speech y esperaba su resultado antes de completar el clasificador local de hablante, por
lo que dos trabajos independientes podían sumar sus esperas en el camino crítico de cada turno.

## Decisión

1. El endpoint conserva la histéresis RMS existente, pero reduce el silencio final a 800 ms.
2. El número de buffers se calcula con techo para todos los intervalos admitidos; nunca termina
   antes del presupuesto de silencio ni añade más de un intervalo.
3. Al detener el micrófono se solicita inmediatamente el cierre de Speech y del analizador de
   identidad. La espera de identidad ocurre mientras Speech produce su resultado final, en lugar de
   comenzar después.
4. Se mantienen reconocimiento, VAD e identidad totalmente on-device. No se incorpora una librería
   ni un modelo adicional porque no elimina ninguna de estas esperas y aumentaría memoria continua.

## Consecuencia

El turno comienza a planificarse unos 400 ms antes por política de endpoint y puede ahorrar hasta
otros 600 ms cuando Speech y la identidad tardan simultáneamente en finalizar. La pausa todavía es
suficientemente larga para filtrar caídas breves ya cubiertas por la histéresis; el máximo de captura,
la cancelación, los permisos y la ausencia de retención de PCM no cambian.
