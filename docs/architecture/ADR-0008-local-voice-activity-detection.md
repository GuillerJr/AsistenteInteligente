# ADR-0008: Detección local y determinista de turnos de voz

- Estado: aceptado
- Fecha: 2026-08-18

## Contexto

Una medición instantánea de amplitud produce parpadeos cuando el ruido cruza el umbral y no permite
identificar el inicio y fin de una intervención. El HUD, la futura transcripción y el control de
turnos necesitan una señal estable que no envíe audio a servicios externos ni descargue un modelo.

## Decisión

`AegisAudioCore` incorpora un VAD determinista basado en RMS y dos umbrales. Por defecto exige dos
frames consecutivos sobre `0.02` para abrir un turno y ocho frames bajo `0.008` para cerrarlo. La
separación de umbrales introduce histéresis: un pico aislado no inicia una intervención y una pausa
breve no la termina.

Cada turno recibe un UUID efímero. Los eventos `started` y `ended` incluyen únicamente ese UUID, la
secuencia de la muestra, tiempo monotónico y, al finalizar, duración. No contienen PCM, texto ni
identidad biométrica. Muestras repetidas o con tiempo no monotónico no pueden modificar el estado.

El helper incluye el evento opcional en la misma envoltura `audio.meter` que la muestra que lo
originó. El daemon acepta ambos atómicamente mediante `audio.meter.publish` y valida:

- coincidencia exacta de secuencia y reloj monotónico;
- transición `idle -> speaking -> idle`;
- correspondencia del UUID al cerrar el turno;
- presencia de duración solo para `ended`;
- `voice_active=true` al iniciar.

El daemon conserva solo el evento más reciente y el UUID activo. Un evento inválido rechaza toda la
publicación, por lo que no deja una muestra parcialmente aplicada.

## Consecuencias

El HUD puede reaccionar a turnos estables y la futura transcripción puede usar los mismos límites.
Este VAD no reconoce el *wake word* ni interpreta contenido. Los umbrales deberán calibrarse con
telemetría local no sensible antes de habilitar captura en segundo plano.
