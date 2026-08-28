# ADR-0194: stream de voz monotónico y acotado

## Estado

Aceptada.

## Contexto

El límite de 2.000 caracteres solo se aplicaba a respuestas no progresivas. Un stream largo podía
pronunciar más contenido y una instantánea que reemplazara texto anterior podía terminar hablando
dos versiones contradictorias. Una respuesta final vacía también podía cerrar el job sin feedback.

## Decisión

1. El daemon rechaza contenido final vacío o compuesto solo por espacios antes de persistirlo.
2. La app normaliza espacios y exige que cada instantánea sea prefijo monotónico de la siguiente.
3. Una divergencia queda bloqueada hasta `reset`, cancela el job y usa feedback local acotado.
4. El chunker permite como máximo 2.000 caracteres hablados durante todo el turno.
5. No se añade dependencia, reintento remoto, sondeo ni segunda respuesta de modelo.

## Consecuencias

Una respuesta progresiva conserva el mismo límite que una respuesta completa, no puede reescribir
audio ya pronunciado y nunca guarda un turno vacío. El control se prueba como lógica pura y la
cancelación reutiliza el IPC autenticado existente.
