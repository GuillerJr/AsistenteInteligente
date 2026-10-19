# Pulido 7 — Voz real y voz única

Fecha: 2026-09-14. Base de trabajo: `eed2427` más el pulido de seguridad local previo, conservado.

Continuación: [personalidad y cadencia de Ray](RAY_DELIVERY_POLISH.md), con nuevas pruebas y
comparación auditiva; mantiene la selección de voz y los límites de privacidad aquí descritos.
Después de la audición, el propietario rechazó el estilo Calm por demasiado relajado: el default
actual es Ray Neutral. Este informe conserva la evidencia histórica de la elección inicial.

## Voz seleccionada con evidencia real

El propietario pidió eliminar Apple y sustituir Diego. Se consultaron la
[documentación oficial de voces](https://docs.nvidia.com/nim/speech/latest/tts/voices.html) y el
[contrato del endpoint](https://build.nvidia.com/nvidia/magpie-tts-multilingual/api).
Después se consultó `list_voices` autenticado con el cargador Keychain existente, sin mostrar ni
persistir la credencial. El despliegue actual no anunciaba las variantes españolas Jason/Ray/Leo
de la matriz general; sí anunciaba sus identidades EN-US con estilo Calm.

Se generaron muestras con `language=es-US` y una frase pública sintética de presentación:

| Identidad | PCM recibido | Resultado |
| --- | ---: | --- |
| EN-US.Jason.Calm | 591872 bytes | audio válido |
| EN-US.Ray.Calm | 473088 bytes | audio válido; **seleccionado por el propietario** |
| EN-US.Leo.Calm | 489472 bytes | audio válido |

Formato: PCM Int16 mono a 22050 Hz, guardado en WAV privado temporal solo para esta audición y
mostrado al usuario. No se usó audio del propietario, contenido de conversaciones, una grabación
de terceros o clonación. La aplicación normal no guarda estos WAV ni rota entre las tres voces.

## Regresiones

- Default Ray Calm con idioma español, tanto en streaming como en la ruta WAV de compatibilidad.
- Sin sintetizador Apple, selección de voz local ni cambios de timbre por falta de IPC/latencia.
- `SpeechOutput` real probado: finalización única, fallo persistente hasta cerrar el texto,
  fragmentos tardíos ignorados, stop/interrupción invalidan callbacks, texto vacío/grande rechazado
  y replay ausente permanece local y silencioso.
- Replay del PCM exacto, digest de texto, caducidad, reloj regresivo, reset explícito y límites de
  bytes/bloques. Se rechazan PCM impar, bloques vacíos/grandes y metadatos inválidos.
- Estado de reproducción separado del resultado del LLM: sin falso completado ni continuidad tras
  error. Texto de contingencia seleccionable en el HUD, borrado en transiciones de privacidad.
- Guardas de integración sobre el código Swift: ninguna segunda síntesis, avisos locales directos
  sin red/replay y pregunta pública de navegador con Ray antes de capturar la elección. No se envía
  el inventario de navegadores ni el contenido de la búsqueda a TTS para esa pregunta.
- Cuatro regresiones Python reproducidas antes del arreglo: apertura cancelada retenía capacidad,
  una lectura fallida retenía sesión, un gestor cerrado aceptaba audio nuevo y una lectura tardía
  revivía una sesión ociosa caducada.

Las guías SwiftPM, Build/Run/Debug y Test Triage guiaron compilación/pruebas del componente real;
SwiftUI Patterns mantuvo el texto largo dentro del HUD desplazable, fuera del menú compacto.

## Validación final

- Configuración efectiva del workspace: `Magpie-Multilingual.EN-US.Ray.Calm`, idioma `es-US`.
  Se imprimieron únicamente esos dos campos, nunca credenciales o el entorno completo.
- Python completo: **1615 aprobadas, 17 omitidas**, sin fallos.
- SwiftPM: app y helpers compilados; **257 pruebas en 14 suites**, todas aprobadas.
  Persisten advertencias de rutas de frameworks de Command Line Tools ausentes, sin impedir
  compilación o enlace. Las pruebas ejecutables ejercitan el reproductor real sin salida audible;
  las tres guardas Python de integración son comprobaciones del código, no una prueba de UI.
- Ruff y `git diff --check`: correctos.
- Aceptación local: **25/25**. Flujos locales de producción: **20/20**.
- Fiabilidad local: **400 ejecuciones, 1160/1160 checkpoints**, sin red ni tareas asíncronas
  filtradas; crecimiento RSS de 294912 bytes, dentro del presupuesto de 8 MiB.

Los cambios permanecen locales: no se ha publicado, sustituido ni reiniciado la app instalada.
Las mejoras previas de seguridad y los archivos del propietario se conservan.

## Reproducción

```bash
.venv/bin/ruff check src tests
.venv/bin/python -m pytest tests/test_config.py tests/test_speech_service.py \
  tests/test_nvidia_client.py tests/test_cli.py tests/test_single_voice_wiring.py
.venv/bin/python -m pytest -o addopts='' -q
./script/test_native.sh
./script/aegis.sh acceptance-benchmark
./script/aegis.sh production-workflows
./script/aegis.sh long-horizon-reliability
```

Las pruebas automatizadas usan proveedores falsos y no reproducen sonido ni abren el micrófono.
Las tres audiciones reales fueron llamadas explícitas, independientes de la batería de regresión.
No se afirma haber cualificado una conversación acústica completa ni medido latencia sostenida.
La tarjeta nueva del HUD está compilada, pero no se ha verificado visualmente en la app instalada.
No se modifican la voz del sistema, TCC, modelos, datos personales o credenciales. Los overrides
explícitos de `AEGIS_NVIDIA_TTS_VOICE` conservan prioridad y deben apuntar a Ray Calm si existían.
