# ADR-0107: cantidades naturales para el temporizador local

- Estado: aceptado
- Fases: 3 y 5

## Decisión

1. La gramática cerrada del temporizador acepta, además de cifras, cantidades habladas entre uno y
   sesenta en español o inglés. Esto cubre transcripciones naturales como «cinco minutos» y
   «thirty seconds».
2. Foundation `NumberFormatter` genera una tabla inmutable por idioma una sola vez. No se incorpora
   un parser lingüístico, diccionario mantenido a mano, proceso, dependencia o modelo.
3. Guiones, mayúsculas y diacríticos se normalizan únicamente dentro de la cantidad. La frase
   completa debe conservar la estructura exacta del temporizador y una unidad explícita.
4. Los alias singulares `un`, `una` y `a` equivalen a uno. Las palabras mayores que sesenta y las
   cantidades vagas se rechazan; las cifras conservan el límite final existente de 24 horas.
5. La ejecución sigue siendo una tarea Swift efímera, monotónica y cancelable. No usa daemon, red,
   memoria, IPC, persistencia ni permisos nuevos después del preflight normal del turno.

## Motivo

Apple Speech puede representar la misma cantidad como cifra o palabra. Exigir dígitos hacía que una
orden válida dependiera de una decisión de formato del transcriptor. Resolver un rango cotidiano con
una API nativa mantiene latencia constante y evita enviar la frase a un modelo.

## Límites

- No se interpretan fracciones, cantidades aproximadas ni expresiones como «un par de minutos».
- No es un recordatorio persistente: cerrar o actualizar Jarvis cancela el temporizador.
- Una frase compuesta o fuera de la gramática conserva el cerebro híbrido habitual.
