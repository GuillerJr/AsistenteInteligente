# ADR-0141: prefetch de un solo segmento hablado

- Estado: aceptado
- Fecha: 2026-08-27
- Fases: 3 y 5
- Extiende: ADR-0140

## Decisión

1. Cuando NVIDIA ya terminó de entregar el segmento actual, aún queda audio reproduciéndose y
   existe otro segmento completo en cola, la app abre en segundo plano únicamente ese stream.
2. El prefetch consume y cierra su sesión IPC antes del handoff. Conserva solo PCM validado en
   memoria, hasta 1 MiB o 64 bloques, y nunca se escribe en disco.
3. Cada segmento se vincula a un UUID local. Un resultado tardío, secuencia inválida o resultado de
   otro segmento se descarta.
4. Interrumpir, detener o entrar en fallback cancela tanto el prefetch en cola como el que espera el
   handoff. La tarea propietaria siempre ejecuta `speech.stream.close` si obtuvo un token.
5. El primer segmento mantiene su presupuesto de 1,8 segundos. Un handoff prefetched dispone de 350
   ms; si no está listo, la voz local continúa el resto de la respuesta sin repetir audio.

## Motivo

El streaming redujo el tiempo al primer audio, pero cada cláusula posterior todavía podía pagar una
nueva espera de red después de terminar la anterior. Preparar toda la respuesta consumiría memoria,
adelantaría texto innecesario y dificultaría la interrupción. Un solo segmento aprovecha el tiempo
de reproducción actual y mantiene backpressure estricto.

## Consecuencias

- Las frases consecutivas pueden enlazarse sin una nueva pausa de síntesis.
- Solo existe una solicitud TTS activa; el siguiente stream abre después de cerrar el proveedor
  anterior y aprovecha los buffers que todavía están reproduciéndose.
- Una interrupción no deja audio, archivo ni sesión remota huérfana.
- Si no existe un segmento completo en cola, no se realiza trabajo especulativo.
