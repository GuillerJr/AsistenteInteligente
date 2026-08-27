# ADR-0151: clic visual ligado dos veces a Accessibility

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Evidencia

El helper ya exigía `AXPress`, propietario correcto y aplicación al frente, pero una decisión de
visión aún podía expresar solo coordenadas. Si la interfaz cambiaba entre captura y acción, esas
coordenadas podían resolver a otro control accesible no sensible.

## Decisión

1. `click` requiere `target`, la etiqueta exacta y acotada del control Accessibility observado.
2. El modelo solo puede copiar `target`, `x` e `y` de un elemento local no sensible, pulsable y con
   origen `accessibility`; OCR o coordenadas libres no conceden autoridad.
3. Python comprueba que los cinco datos del clic pertenecen al mismo elemento de la observación.
4. El helper Swift firmado resuelve de nuevo el elemento bajo el punto, asciende hasta el control
   que ofrece `AXPress` y vuelve a producir su etiqueta con el mismo descriptor usado al capturarlo.
5. Una etiqueta ausente o diferente falla como `unsafe_target` antes de `AXPress`. No existe
   corrección aproximada, segundo clic, movimiento del cursor ni fallback a eventos de mouse.
6. El retículo de Jarvis solo se anima para órdenes que también incluyen una etiqueta acotada.

## Consecuencia

Un modelo puede escoger entre controles visibles, pero no convertir una coordenada de la captura en
autoridad autónoma. La protección añade comparaciones locales sin inferencia, dependencia, estado
persistente ni latencia de red. Interfaces canvas o sin semántica Accessibility permanecen fuera del
clic genérico y requieren herramientas de dominio con contratos explícitos.
