# ADR-0156: finalización visual ligada a evidencia local

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

El rol de visión podía devolver `done` sin campos. La decisión dependía de su interpretación de la
captura, de modo que una afirmación equivocada o texto visual no confiable podía cerrar la sesión
como objetivo completado sin una prueba local identificable.

## Decisión

1. `done` exige una única cadena `evidence`, imprimible y acotada a 256 caracteres.
2. El controlador solo la acepta si coincide exactamente con el título de una ventana o el texto de
   un elemento Accessibility no sensible de la misma observación.
3. OCR, texto presente solo en el JPEG, coincidencias aproximadas y evidencia inventada no autorizan
   finalización. El resultado es `blocked/uncertain_state` y el helper no se ejecuta.
4. Un `done` sin evidencia viola el contrato y entra en el único intento de reparación ya existente.
5. La comprobación reutiliza la percepción local incluida en el paso; no añade captura, inferencia,
   dependencia, persistencia ni permiso.

## Consecuencia

La semántica final continúa siendo responsabilidad del especialista visual, pero su afirmación debe
quedar anclada a estado local confiable y auditable por contrato. Una página puede aportar contexto
por imagen u OCR, pero no puede fabricar por sí sola la prueba que termina el ciclo.
