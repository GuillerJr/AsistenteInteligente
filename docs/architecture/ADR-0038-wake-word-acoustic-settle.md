# ADR-0038: Reanudación acústicamente estable del wake word

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** recrear el analizador después de hablar reinicia su cooldown y puede escuchar la
   cola acústica de la propia respuesta.
2. **Eliminar:** no se añade cancelación de eco, DSP, temporizador persistente ni dependencia.
3. **Simplificar:** una compuerta monotónica exige 750 ms continuos sin audio activo.
4. **Acelerar:** el mismo bucle de reanudación pasa de espera binaria a estabilidad comprobable.
5. **Automatizar:** nueva actividad reinicia la compuerta sin intervención del usuario.

## Decisión

`WakeWordResumeGate` recibe únicamente el estado agregado de captura, síntesis y enrolamiento, junto
con `systemUptime`. Rechaza tiempos no finitos o no crecientes. Cuando el audio queda libre inicia un
intervalo de 750 ms; cualquier actividad antes de completarlo borra el inicio.

El coordinador de la Menu Bar evalúa la compuerta cada 100 ms y respeta la cancelación de la tarea y
el opt-in antes de reabrir `AVAudioEngine`. El modelo, los buffers y el texto no participan en esta
decisión.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** separa la salida local de la siguiente captura.
- **Fase 5 — Voice-first:** reduce autoactivaciones sin mantener una interfaz visible.

## Consecuencia

Jarvis tarda como mínimo 750 ms en volver a escuchar después de un turno o enrolamiento. Ese coste
acotado evita depender del cooldown efímero de un analizador recién creado y no afecta una
activación manual cuando el stream estaba apagado.
