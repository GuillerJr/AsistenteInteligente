# Ray Neutral: fluidez

Fecha: 2026-09-14. Se conserva el único locutor Ray Neutral y la personalidad despierta y resolutiva.

## Contexto y pausas

La unidad oral pasa de 48 a 120 caracteres, sin esperar a 120 si ya terminó la respuesta.
La muestra de 136 caracteres se sintetiza ahora en una sola petición; una prueba Swift reproduce
su llegada carácter a carácter. Una oración sustancial todavía puede empezar antes de terminar
toda la respuesta. Esperar más contexto puede retrasar el primer audio; no se promete menor
latencia. La búsqueda de corte sigue limitada a 320 caracteres, con las protecciones anteriores.
La guía de diálogo también evita separar reconocimientos breves del pensamiento que los sigue,
sin reescribir la puntuación de datos, citas o mensajes críticos mediante un reemplazo automático.

En la anterior muestra Neutral se midió un tramo de 611 ms con amplitud absoluta no mayor de
32 unidades Int16 después de «Aquí estoy». `SpeechPauseSmoother` acorta solo esos tramos casi
inaudibles: hasta 80 ms iniciales y 300 ms después de señal por encima de ese umbral. Las muestras
de mayor amplitud se conservan byte a byte; no cambia pitch, ganancia ni frecuencia de muestreo.
No es un detector de fonemas: puede acortar contenido extremadamente tenue o pausas deliberadas.
Puede desactivarse con `AEGIS_NVIDIA_TTS_SMOOTH_PAUSES=false`.

Solo se aplica a PCM mono Int16 de 22050 Hz de NVIDIA, antes de IPC, reproducción y replay. No
se usa con micrófono o biometría. Su estado consta de un byte incompleto, contadores y un indicador
por solicitud. El límite de 8 MiB se calcula sobre el audio original, incluidos los silencios
eliminados, y se conserva cancelación cooperativa. El PCM incompleto se rechaza al cerrar.
No modifica el WAV de compatibilidad ni añade parámetros al proveedor. El replay conserva el PCM
ya reproducido y no vuelve a procesarlo.

## Evidencia y límites

Se generó una inferencia real con texto público sintético de 136 caracteres, sin voz del propietario
ni datos personales. El script de audición aplicó la misma clase de procesamiento que producción
para comparar antes/después sobre **la misma toma**, sin variabilidad entre inferencias:

| PCM | Bytes | Duración |
| --- | ---: | ---: |
| Contexto continuo, sin ajustar | 444416 | 10,077 s |
| Contexto continuo, pausas ajustadas | 429220 | 9,733 s |

Se retiraron 345 ms casi inaudibles. El procesamiento empleó 23,26 ms en esta ejecución, no un
benchmark sostenido. Los WAV son muestras privadas temporales. La audición no reproduce IPC,
altavoces, micrófono ni las condiciones de red de una conversación completa; no certifica que
el timbre sintético de Magpie desaparezca. La naturalidad percibida sigue sujeta al usuario.

## Validación

- Focalizadas: 101 Python y 23 Swift aprobadas.
- Completas: **1638 Python aprobadas, 17 omitidas; 266 Swift en 15 suites aprobadas**.
- Ruff y comprobación del diff correctos.
- Jarvis, AegisAudioCore y los helpers compilan con `./script/test_native.sh`; las advertencias
  conocidas de rutas de frameworks de Command Line Tools no impiden compilación o enlace.

Se prueban cortes impares de transporte, invariancia frente a paquetización, muestras límite,
pausas cortas intactas, cola final, separación entre solicitudes, PCM incompleto, límite de bytes
anterior al ajuste y selección Ray Neutral. La guía SwiftPM orientó la compilación y las pruebas
del segmentador real, preservando los cambios anteriores de seguridad y voz única.

```bash
.venv/bin/python -m pytest tests/test_speech_pcm.py tests/test_nvidia_client.py tests/test_speech_service.py tests/test_dialogue.py tests/test_config.py -o addopts='' -q
./script/test_native.sh --filter 'SpeechStreamChunkerTests|SpeechPlayoutPolicyTests|SpeechOutputTests|SpeechAudioReplayBufferTests'
.venv/bin/python -m pytest -o addopts='' -q
./script/test_native.sh
.venv/bin/ruff check src tests
```

Cambios locales, sin instalar ni publicar. Pendientes valoración auditiva y prueba completa en la app.
