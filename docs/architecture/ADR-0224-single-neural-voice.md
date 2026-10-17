# ADR-0224: una sola identidad vocal, Ray

Fecha: 2026-09-14. Estado: aceptado por elección del propietario tras comparar muestras.

Actualización de estilo: el propietario rechazó posteriormente Calm por demasiado relajado.
El default vigente es `Magpie-Multilingual.EN-US.Ray.Neutral`, verificado en catálogo y síntesis
real en español. Se mantiene Ray como único locutor; Neutral es el ajuste adoptado, pendiente
de su valoración auditiva. Las decisiones siguientes registran la selección inicial de Calm.

## Decisión

La voz por defecto es `Magpie-Multilingual.EN-US.Ray.Calm`; el texto conserva el idioma `es-US`.
El endpoint actual confirmó esa identidad y generó correctamente una muestra en español. El código
de locale de la identidad no cambia el idioma de la respuesta. La documentación NVIDIA describe
la [combinación de identidad e idioma](https://docs.nvidia.com/nim/speech/latest/tts/voices.html).
No se presupone que el catálogo desplegado coincida con todas las voces de la matriz documental.

Se retira por completo `AVSpeechSynthesizer` de la salida de Jarvis. No hay selección dinámica de
voces de macOS ni fallback por latencia, falta de IPC o fallo de proveedor. Apple Speech continúa
como reconocedor local de entrada; no se alteran wake word, enrolamiento, TCC o biometría.

`SpeechOutput` pasa a AegisAudioCore para probar el componente real. La finalización devuelve un
resultado explícito: completado, no disponible, interrumpido, replay ausente o texto inválido. Un
fallo durante la generación es permanente para ese stream; no se reinicia con fragmentos tardíos.
La finalización de un stream fallido espera al cierre del texto para conservar el resultado final.
Solo la reproducción correcta abre continuidad y registra evidencia de playback completado.

El inicio remoto y la entrega del prefetch tienen un máximo de quince segundos. El reproductor
conserva colas PCM acotadas y limita todo el texto a 2.000 escalares Unicode. Interrumpir invalida
callbacks antes de esperar el fade de 150 ms. No se inicia un locutor sustituto.

La orden privada de repetición mantiene las compuertas de propietario/presencia. Reproduce el PCM
original completo desde RAM, ligado mediante digest al texto normalizado: hasta 4 MiB, 4096 bloques
y cinco minutos de validez. No repite inferencia, TTS, transmisión o persistencia. Una toma parcial,
inválida, demasiado grande o cancelada no se convierte en nueva grabación repetible. Bloqueo,
reposo y reinicio conversacional limpian también audio y texto de presentación. Sin audio válido,
el HUD informa y no habla con otra identidad.

Los avisos de fallo continúan sin red: se presentan directamente como texto en el HUD, sin intentar
síntesis ni replay ante un fallo de daemon/proveedor. La elección de navegador usa una pregunta
pública fija con Ray y después escucha la elección; el inventario de navegadores permanece local
y los botones siguen disponibles si falla TTS. Los resultados ordinarios y utilidades que ya
usaban NVIDIA conservan su ruta y ahora solo usan Ray. El texto de contingencia
es efímero y seleccionable; no entra en la presentación compacta del notch ni en telemetría.

El daemon retira sesiones ante cancelación de apertura, error/cancelación de lectura o TTL agotado.
Cerrar el gestor impide aperturas tardías; un conflicto de secuencia no destruye un stream válido.
No se añaden reintentos, endpoints de síntesis, credenciales ni modelos descargados.

## Sustituciones y límites

Sustituye la selección híbrida de voz de ADR-0070, ADR-0087 y ADR-0140, la síntesis Apple de avisos
de ADR-0195 y la resíntesis local de ADR-0196. Mantiene sus límites de seguridad y privacidad.

Ray Calm es una voz de catálogo elegida por el usuario, no una clonación ni una promesa de imitar
al personaje cinematográfico. Sin conexión no puede producir frases nuevas; solo puede repetir
PCM válido ya disponible. Las muestras validan el endpoint y la preferencia, no certifican el
circuito completo de micrófono, altavoces, ruido ambiental o latencia sostenida.

Regresiones y reproducción en [REAL_VOICE_POLISH.md](../quality/REAL_VOICE_POLISH.md).
