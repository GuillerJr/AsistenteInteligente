# ADR-0028: Turno de voz acotado por silencio

- Estado: aceptado
- Fecha: 2026-08-20

## Filtro de ingeniería

1. **Cuestionar:** ocho segundos fijos no representan la duración real de una orden hablada.
2. **Eliminar:** no se añade otro modelo, framework de audio ni temporizador en la interfaz.
3. **Simplificar:** la transcripción reutiliza el VAD local que ya procesa el mismo búfer PCM.
4. **Acelerar:** el turno termina al detectar silencio y conserva límites deterministas.
5. **Automatizar:** el mismo endpointing sirve al menú, atajo, imagen, pantalla y futuro *wake word*.

## Decisión

Jarvis espera hasta ocho segundos para el inicio de voz. Después de detectarlo, finaliza la captura
tras 1,2 segundos continuos de silencio. Un límite absoluto de 60 segundos evita una sesión abierta
por ruido, fallo del VAD o entrada continua. La finalización o el error de Apple Speech también
interrumpen inmediatamente la espera.

El VAD opera dentro del proceso nativo y solo entrega eventos efímeros. No persiste PCM ni lo envía
al daemon, a NVIDIA o a la interfaz. Los eventos de fin deben pertenecer al mismo UUID de utterance
que abrió el turno.

## Consecuencias

Las órdenes cortas terminan antes y las largas dejan de truncarse a los ocho segundos. El usuario
conserva una activación explícita; el detector opcional de “Jarvis” podrá reutilizar este endpointing
sin modificar la frontera de privacidad.
