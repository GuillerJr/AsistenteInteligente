# ADR-0161: desplazamiento visual ligado a la aplicación

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Evidencia

El helper publicaba un `CGEvent` de rueda sin ubicación explícita. macOS podía resolver su destino a
partir del cursor nativo del usuario, aunque Jarvis hubiera autorizado y capturado otra aplicación.
Mover ese cursor para corregir el destino violaría la separación entre el puntero del usuario y la
representación visual de Jarvis.

Las acciones Accessibility privadas de desplazamiento por página no forman parte del contrato
público estable del SDK activo y no ofrecen cobertura uniforme entre navegadores y aplicaciones.

## Decisión

1. `scroll` exige la aplicación autorizada también dentro del ejecutable nativo.
2. El helper obtiene la ventana Accessibility enfocada del proceso frontal y valida su propietario,
   posición y tamaño.
3. `ComputerScrollPlan` calcula el centro solo si pertenece a la pantalla principal y conserva las
   cuatro direcciones con intensidad de uno a ocho.
4. El elemento Accessibility bajo ese punto debe pertenecer al mismo bundle. Se comprueba al calcular
   el objetivo y otra vez inmediatamente antes de publicar.
5. El evento de rueda recibe esa ubicación explícita. No lee ni mueve el cursor nativo.
6. Ventana ausente, geometría inválida, otra app superpuesta o cambio de aplicación termina como
   objetivo inseguro; no existe fallback al cursor ni a una API privada.
7. La recaptura y prueba de progreso posteriores permanecen obligatorias.

## Consecuencia

El desplazamiento sigue siendo compatible con aplicaciones que no implementan acciones AX privadas,
pero queda ligado geométricamente a la ventana autorizada. El usuario conserva control independiente
de su cursor y cualquier ambigüedad de destino falla cerrada sin dependencia nueva.
