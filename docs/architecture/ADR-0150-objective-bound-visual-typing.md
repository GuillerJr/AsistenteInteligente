# ADR-0150: escritura visual ligada al objetivo aprobado

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

`ComputerAction` limitaba tamaño y caracteres de `type`, pero el modelo aún podía proponer cualquier
texto. Una instrucción maliciosa dentro de la captura podía intentar convertir datos visuales no
confiables en texto escrito por Jarvis, aunque el usuario nunca lo hubiera incluido en el objetivo.

## Decisión

1. Antes de ejecutar `type`, controlador y objetivo se normalizan localmente con el mismo plegado
   Unicode usado por la percepción.
2. El texto completo debe aparecer como una frase delimitada dentro del objetivo aprobado.
3. Una escritura no ligada termina como `blocked/unsupported_action` sin llamar al helper.
4. La instrucción visual exige copiar una frase literal del objetivo y prohíbe texto presente solo
   en la captura.
5. Clic, desplazamiento, espera y teclas conservan sus contratos independientes. No se relaja la
   confirmación, la app autorizada ni la protección de contenido seguro.

## Consecuencia

El modelo puede ubicar el campo y decidir cuándo escribir, pero no inventar el contenido escrito.
Esto corta una vía de prompt injection sin otra inferencia, dependencia, persistencia o latencia de
red.
