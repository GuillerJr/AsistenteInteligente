# ADR-0101: calculadora decimal local de gramática cerrada

- Estado: aceptado
- Fases: 1 y 5

## Decisión

1. Una petición empieza por `calcula`, `cuánto es`, `calculate` o `what is` y debe contener
   exactamente dos operandos y un operador permitido.
2. Cada operando admite signo, hasta 18 dígitos enteros y seis decimales. Los operadores se reducen
   a suma, resta, multiplicación o división mediante un mapa cerrado.
3. El cálculo usa `decimal.Decimal` con precisión acotada. No usa `eval`, AST, shell, herramientas,
   procesos, memoria o dependencias nuevas.
4. Los resultados muestran hasta diez decimales y señalan cuando fueron redondeados. La división por
   cero produce una negativa fija; magnitudes no representables con utilidad dentro del contrato
   vuelven al cerebro normal.
5. El resultado se publica como un único fragmento `local/deterministic-calculator`. Adjuntos,
   transcripción no local y `force_remote` desactivan la ruta.

## Motivo

Delegar aritmética binaria explícita a un LLM añade latencia y puede producir un número incorrecto.
Una gramática cerrada elimina ejecución arbitraria y mantiene el comportamiento verificable. En
1.000 recorridos completos del grafo se observaron cero llamadas de modelo y 1,04 ms de latencia
promedio.

## Límites

- No interpreta palabras numéricas, unidades, monedas, porcentajes, potencias ni prioridades.
- No acepta más de un operador ni encadena resultados entre turnos.
- Los cálculos que no coinciden completamente con la gramática conservan el cerebro híbrido; no se
  intenta reparar una expresión parcialmente válida.
