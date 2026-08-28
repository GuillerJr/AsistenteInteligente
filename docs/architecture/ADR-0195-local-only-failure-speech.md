# ADR-0195: voz de fallo exclusivamente local

## Estado

Aceptada.

## Contexto

Los mensajes de fallo se construían localmente, pero reutilizaban la salida normal que prioriza
NVIDIA Magpie. Un error de daemon, IPC o proveedor podía intentar depender otra vez del componente
que acababa de fallar, añadiendo hasta 1,8 segundos de espera antes del fallback.

## Decisión

1. `SpeechOutput` expone una ruta local explícita basada en `AVSpeechSynthesizer`.
2. `announceVoiceFailure` usa siempre esa ruta, exista o no un secreto IPC disponible.
3. La ruta local conserva interrupción por wake word y reanudación posterior del detector.
4. Respuestas exitosas mantienen NVIDIA Magpie con el fallback local existente.
5. No se añade proveedor, dependencia, retry, estado persistente ni llamada de red.

## Consecuencias

Los fallos se oyen sin depender de NVIDIA o del daemon y eliminan el presupuesto remoto de 1,8
segundos de ese camino. La telemetría distingue `provider=apple mode=local_only` de un fallback por
indisponibilidad.
