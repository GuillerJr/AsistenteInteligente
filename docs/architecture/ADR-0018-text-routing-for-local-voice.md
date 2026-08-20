# ADR-0018: Routing textual para voz transcrita localmente

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** marcar el origen como `audio` no significa que el especialista reciba audio.
2. **Eliminar:** no se añade fallback remoto, conversión de formatos ni reintento oculto.
3. **Simplificar:** el flag local `speech_on_device` basta para distinguir transcript de audio crudo.
4. **Acelerar:** se conserva el mismo grafo y solo cambia su decisión de routing.
5. **Automatizar:** daemon y pruebas cargan siempre `src/`, evitando copias Python obsoletas.

## Decisión

`voice.submit` conserva modalidades `text` y `audio` como trazabilidad, pero el router recibe además
`local_voice_transcript=true`. En ese caso enruta por el significado del texto; una frase general va
a `planner`, mientras código o seguridad siguen yendo a `code_security`. `omni` queda reservado para
solicitudes que realmente contengan audio o vídeo multimodal.

Si el router devuelve una respuesta inválida, el fallback compara palabras completas y no
subcadenas. Reconoce términos de código y seguridad en español e inglés, incluidos SIP, Gatekeeper,
FileVault y firewall; por tanto, `pared` no coincide accidentalmente con `red`.

El LaunchAgent ejecuta Python directamente con `PYTHONPATH=<workspace>/src`. El wrapper
`script/aegis.sh` aplica el mismo contrato en terminal y pytest declara `pythonpath=["src"]`. Esto
mantiene la instalación de dependencias `--no-editable` sin ejecutar una copia vieja del proyecto.

## Verificación

- Router, planner y synthesizer NVIDIA respondieron a sondas mínimas; el endpoint de `omni` falló.
- El suite Python completo pasa fuera del sandbox; el único skip es preexistente.
- Las 21 pruebas Swift pasan en arm64.
- Una vuelta real produjo `voice_turn_started`, `voice_turn_submitted` y
  `voice_turn_completed`, sin registrar audio, transcript, respuesta ni identificadores.

## Encaje en el roadmap

- **Fase 1:** corrige la asignación del especialista según el payload realmente disponible.
- **Fase 3:** completa la vertical voz → transcript local → swarm → respuesta hablada.
