# ADR-0145: cancelación TTS ligada al turno de voz

- Estado: aceptado
- Fecha: 2026-08-27
- Fases: 1, 3 y 5
- Extiende: ADR-0084 y ADR-0140

## Evidencia

La app cancelaba su `Task` y detenía AVAudioEngine inmediatamente. Sin embargo,
`LocalIPCClient.openSpeechStream` es una llamada síncrona: mientras esperaba el primer bloque no
conocía todavía el token de sesión y no podía ejecutar `speech.stream.close`. La petición NVIDIA
podía continuar en el daemon hasta el timeout aunque el usuario ya hubiese interrumpido a Jarvis.

## Decisión

1. Cada `SpeechOutput.beginStream` genera un `group_token` aleatorio de 128 bits antes de abrir TTS.
2. `speech.stream.open` exige ese grupo y el daemon lo asocia a la sesión antes de solicitar el
   primer fragmento al proveedor.
3. `speech.stream.cancel` acepta únicamente un grupo canónico autenticado. El manager elimina sus
   sesiones, cancela la tarea que está dentro de `anext()` y cierra el generador asíncrono.
4. Detener audio, cambiar a voz local o iniciar otra respuesta envía la cancelación del grupo
   anterior. El grupo nuevo es distinto, por lo que una cancelación tardía no puede alcanzarlo.
5. Token individual, secuencia, límites de cuatro sesiones, 8 MiB, TTL y fallback local permanecen
   vigentes. Un grupo no concede acceso a audio ni devuelve texto.

## Consecuencias

La interrupción deja de ser solo perceptiva: termina también red, generación PCM y buffers del
turno sustituido. Una prueba con el proveedor bloqueado antes del primer fragmento confirma que el
generador recibe cancelación sin esperar el timeout IPC de 33 segundos. El protocolo añade un
método, pero evita WebSocket, polling o un canal persistente adicional.
