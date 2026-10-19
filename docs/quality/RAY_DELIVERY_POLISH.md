# Ray — personalidad y cadencia

Fecha: 2026-09-14. Continúa el pulido de voz única, conservando los cambios locales de seguridad.

Este documento registra las revisiones anteriores. El ajuste vigente de frases y pausas está en
[Ray Neutral: fluidez](RAY_FLUENCY_POLISH.md); conserva Neutral y sustituye el umbral de 48 por 120.

## Ajuste posterior: menos relajado

El propietario rechazó la entrega Calm por demasiado relajada. Se cambia el default a
`Magpie-Multilingual.EN-US.Ray.Neutral`, manteniendo el idioma `es-US`, el mismo locutor y la
salida única. El catálogo desplegado confirmó Ray, Angry, Calm, Fearful y Neutral; no se inventan
variantes de energía. La [nomenclatura oficial](https://docs.nvidia.com/nim/speech/latest/tts/voices.html)
distingue identidad y estilo emocional.

Una nueva audición usa exactamente los mismos dos textos de la comparación previa: Neutral
entregó 434176 bytes PCM, 9,85 segundos, frente a 11,89 segundos de aquella toma Calm. No se
aceleró el archivo ni se alteró el pitch. Es evidencia de esas dos tomas, no una promesa de
velocidad o naturalidad constante. La aceptación auditiva de Neutral queda pendiente del usuario.

La guía sustituye «cadencia relajada» por una entrega ágil, despierta y resolutiva, sin gritar,
precipitarse o fingir entusiasmo. Se conservan tacto, límites de humor y autorizaciones. No se
modificaron de nuevo el reproductor Swift, la segmentación, el búfer ni las protecciones de replay.
Configuración efectiva verificada: Ray Neutral / es-US. Las **231 pruebas focalizadas** de
configuración, proveedor, CLI, diálogo, orquestación y cableado de voz pasan; Ruff y comprobación
del diff correctos. No se reinstaló la app ni se atribuyen a este ajuste nuevas pruebas Swift.

## Cambios

La guía de diálogo común incorpora una personalidad despierta, perceptiva y precisa, con calidez
contenida e ironía breve solo cuando encaja. Evita saludos repetidos, informes ceremoniales,
adulación y el uso constante de «señor» o «jefe». No permite que el estilo autorice acciones,
invente ejecución o sustituya las preferencias del propietario. En apoyo emocional y correcciones
no hay humor; tampoco corresponde en errores, seguridad o decisiones delicadas.

La escritura oral favorece pensamientos conectados y puntuación corriente. No añade respiraciones
falsas, acotaciones, SSML o muletillas. Conserva precisión en valores, código e identificadores.
La guía ya se propaga al diálogo local y NVIDIA; las instrucciones específicas de ingeniería,
las respuestas deterministas y los textos críticos de autorización no se reescriben.

`SpeechStreamChunker` agrupa frases muy breves hasta una unidad de al menos 48 caracteres,
salvo el cierre de respuesta, que libera el resto. Prioriza finales de oración; una coma ya no
dispara síntesis por sí sola. Al alcanzar 320 caracteres de prosa busca una cláusula y, en su
defecto, un espacio. Un token indivisible no se corta arbitrariamente; se mantiene el límite
global de 2000 caracteres. Respeta títulos, iniciales, abreviaturas horarias, decimales y cierres
de comillas, sin repetir ni alterar el texto incremental.

El reproductor prepara la siguiente unidad desde que empieza el audio actual, sin esperar al
fin de la respuesta PCM del proveedor. Conserva un solo prefetch de hasta 2 MiB y 512 bloques,
además de las colas existentes; no introduce reintentos ni nuevas voces. Una reserva inicial
de 100 ms de PCM amortigua pequeñas variaciones de llegada. EOF libera audios cortos y ocho
slots llenos liberan paquetes diminutos para evitar bloqueos. La reserva no garantiza continuidad
ante una interrupción prolongada de red. Se conservan cancelación, deadline, fade y replay privado.

## Controles NVIDIA verificados

La [API HTTP de Magpie](https://docs.nvidia.com/nim/speech/latest/reference/api-references/tts/http-tts.html)
documenta voz, idioma, PCM y pronunciación, pero no controles de velocidad, pitch ni una instrucción
libre de actuación en esta ruta. La [documentación de personalización](https://docs.nvidia.com/nim/speech/latest/tts/customization.html)
limita el SSML documentado a pronunciación/normalización; `exaggeration_factor` no es un control
de Magpie. Por ello no se envían parámetros inventados ni etiquetas de prosodia no verificadas.
Se conserva la identidad Ray con `es-US`, ahora en estilo Neutral, sin modificar pitch, velocidad
o añadir efectos. No se clona una interpretación cinematográfica.

## Comparación real de segmentación (histórica, con Calm)

Mismo texto público sintético de 136 caracteres y misma identidad vocal, PCM mono Int16, 22050 Hz.
El texto es una demostración redactada para audición, no una respuesta atribuida a un LLM real.

| Muestra | Solicitudes TTS | PCM | Duración |
| --- | ---: | ---: | ---: |
| Segmentación anterior | 3 | 522240 bytes | 11,84 s |
| Nueva segmentación | 2 | 524288 bytes | 11,89 s |

Se concatenó el PCM en WAV privados temporales, sin añadir silencios, filtros o aceleración a
ninguna muestra. Se generó también una toma exploratoria de una sola unidad, descartada de la
comparación porque no representaba la segmentación incremental real. La prueba Swift verifica
carácter a carácter las dos unidades usadas en la muestra final. Las muestras no reproducen IPC,
Core Audio ni jitter de una sesión real; tampoco prueban una mejora acústica universal.

No hubo captura de micrófono, lectura de conversaciones, nueva credencial ni envío de datos
personales. La naturalidad percibida queda sujeta a la valoración del propietario.

## Validación

- Pruebas focalizadas: 176 Python y 23 Swift aprobadas.
- Suite completa: **1624 Python aprobadas, 17 omitidas; 266 Swift en 15 suites aprobadas**.
- Aceptación local **25/25** y flujos de producción **20/20**, sin red.
- Ruff y `git diff --check` correctos.
- Jarvis, la biblioteca AegisAudioCore y los helpers compilan mediante `./script/test_native.sh`.
  Persisten las advertencias conocidas de rutas de frameworks de Command Line Tools, sin fallos.

Las guías SwiftPM y Test Triage guiaron la compilación del reproductor real, las pruebas acotadas
de cadencia y la corrección de una expectativa de muestra, sin confundirla con un fallo del motor.

```bash
.venv/bin/python -m pytest tests/test_dialogue.py tests/test_graph.py -o addopts='' -q
./script/test_native.sh --filter 'SpeechStreamChunkerTests|SpeechPlayoutPolicyTests|SpeechOutputTests|SpeechAudioReplayBufferTests'
.venv/bin/python -m pytest -o addopts='' -q
./script/test_native.sh
.venv/bin/ruff check src tests
./script/aegis.sh acceptance-benchmark
./script/aegis.sh production-workflows
```

Cambios locales, no publicados ni instalados. Pendientes la valoración auditiva del usuario y
una conversación completa con la app actualizada; no se afirma haber realizado esa prueba.
