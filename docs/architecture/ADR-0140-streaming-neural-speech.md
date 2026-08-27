# ADR-0140: voz neural incremental sobre IPC autenticado

- Estado: aceptado
- Fecha: 2026-08-27
- Fases: 1, 3 y 5
- Extiende: ADR-0070 y ADR-0087

## Decisión

1. El daemon consume `synthesize_online` de NVIDIA Magpie como PCM mono Int16 de 22,05 kHz. La
   credencial continúa confinada a Keychain y al proceso Python.
2. `speech.stream.open` devuelve ya el primer bloque. `speech.stream.next` exige la secuencia exacta
   anterior y `speech.stream.close` invalida el token aleatorio. Todas las llamadas reutilizan el
   UDS autenticado con HMAC y comprobación de UID.
3. Cada bloque contiene como máximo 16 KiB; el total queda limitado a 8 MiB. Solo existen cuatro
   sesiones simultáneas, expiran tras 45 segundos y se limpian sin polling.
4. La app valida token, Base64 canónico, secuencia, formato y paridad de muestras. AVAudioEngine
   programa los buffers directamente, sin archivos ni una dependencia de audio nueva.
5. Si no comienza audio remoto en 1,8 segundos, el turno usa AVSpeechSynthesizer. Una interrupción
   posterior no repite audio ya reproducido; los segmentos siguientes usan la ruta local.
6. `speech.synthesize` y su WAV privado permanecen únicamente como contrato compatible y sonda de
   diagnóstico; dejaron de formar parte del camino de conversación.

## Motivo

La ruta anterior esperaba la síntesis completa, escribía un WAV y recién entonces iniciaba la
reproducción. El endpoint alojado ya entrega PCM incremental, por lo que esa espera y el archivo no
aportaban seguridad al recorrido normal. El pull IPC conserva límites, autenticación y backpressure
sin introducir gRPC, WebSocket, proceso residente ni caché.

## Consecuencias

- El primer audio puede sonar antes de que NVIDIA termine la frase.
- No existe audio temporal en el recorrido normal y cada respuesta conserva backpressure nativo.
- La red sigue siendo opcional: el fallback local mantiene el ciclo voice-first.
- Un daemon anterior no ofrece los métodos nuevos; el instalador actualiza el daemon antes que la
  app para evitar incompatibilidad durante el despliegue.
