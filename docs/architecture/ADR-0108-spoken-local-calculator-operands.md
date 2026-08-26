# ADR-0108: operandos hablados para la calculadora local

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. La calculadora determinista acepta enteros hablados entre −100 y 100 en español o inglés, además
   del contrato numérico existente. Ejemplos: «doce por cuatro» y `twenty-one plus four`.
2. El vocabulario se construye una vez desde unidades y decenas cerradas. No se incorpora librería
   lingüística, `eval`, AST, modelo, herramienta, proceso, red o memoria.
3. Cada operando hablado admite hasta cuatro palabras. Guiones, mayúsculas y diacríticos se
   normalizan después de que la expresión completa coincide con la gramática cerrada.
4. Solo se admiten enteros hablados. Decimales, fracciones, porcentajes, unidades, cantidades vagas,
   valores mayores que cien y más de un operador conservan el cerebro híbrido habitual.
5. El cálculo sigue usando `Decimal`, precisión acotada, una respuesta monotónica y cero llamadas de
   modelo. `force_remote`, adjuntos o transcripción no local continúan desactivando esta ruta.

## Motivo

Apple Speech puede representar cantidades pequeñas como palabras. Enviar «doce por cuatro» a un LLM
cuando «12 por 4» ya se resuelve localmente añade latencia y hace depender el resultado del formato
elegido por el transcriptor.

## Límites

- No intenta interpretar lenguaje matemático general ni encadenar el resultado anterior.
- La coincidencia completa evita convertir texto arbitrario en una expresión.
- Los límites de magnitud, redondeo y división por cero del ADR-0101 permanecen intactos.
