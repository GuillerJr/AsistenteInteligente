# ADR-0017: Vuelta de voz explícita y observable

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** una prueba manual sobre un menú inaccesible no demuestra el pipeline completo.
2. **Eliminar:** no se añade grabador, archivo de audio, transcript persistente ni framework de
   telemetría.
3. **Simplificar:** un argumento de arranque reutiliza exactamente `startVoiceTurn()`.
4. **Acelerar:** el script operativo existente expone una sola orden acotada.
5. **Automatizar:** Unified Logging permite verificar etapas sin observar contenido sensible.

## Decisión

`menu_bar_service.sh voice-turn` relanza el bundle instalado con `--voice-turn`. La app valida daemon
y permisos, emite un beep nativo y captura un máximo de ocho segundos. Exige reconocimiento
on-device, envía solo el transcript final por IPC autenticado y pronuncia la respuesta localmente.

La categoría `ai.aegis.menubar/VoiceTurn` emite únicamente inicio, envío, finalización o etapa/código
de fallo. No registra audio, transcript, respuesta, credenciales, `job_id` ni identificadores de
captura. Los diagnósticos de captura distinguen solo ausencia de nivel audible, fallo del recognizer
y ausencia de resultado final; no conservan muestras ni mediciones.

## Encaje en el roadmap

- **Fase 3:** prueba sensorial completa, local y fail-closed.
- **Fase 5:** establece el disparador voice-first reutilizable por menú, atajo o futura activación.

## Verificación del host

La build arm64 y 21 pruebas nativas pasan. La captura real alcanza Apple Speech; durante la prueba
CoreAudio registra `kAudioUnitErr_InvalidElement` (`-10877`) y el recognizer termina con
`recognitionFailed`. Por diseño no se envía ningún transcript ni se crea un job. La corrección del
dispositivo/formato de entrada queda como siguiente corte de Fase 3 y no se oculta con fallback
remoto.
