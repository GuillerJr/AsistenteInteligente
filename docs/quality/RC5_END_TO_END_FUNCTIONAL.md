# RC-5 — Funcionamiento extremo a extremo sin voz

RC-5 prueba el producto que una persona realmente ejecuta, no solo clases aisladas. La revisión
instalada debe coincidir exactamente con `git rev-parse HEAD`; después, `jarvis --request "Hola"`
recorre CLI, UDS firmado, gestor de trabajos y respuesta determinista local. El turno usa
`local_only`, no depende de internet y no necesita la voz del propietario. La prueba adopta el
workspace privado que anuncia el daemon durante `engineering.preflight`; no intenta ampliar por
silencio el perímetro de archivos autorizado.

La misma compuerta ejecuta los 25 contratos de aceptación y los 20 flujos de producción. Esos
flujos cubren conversación, orquestación, memoria cifrada, seguridad, planificación y
automatización AX-first con recuperación de obstáculos.

## Criterio de cierre

- Runtime instalado y firmado con la misma revisión del repositorio.
- Un turno real del CLI termina con código cero.
- Aceptación: 25/25.
- Flujos de producción: 20/20.
- Cero audio, transcripciones o llamadas de red en la evidencia.

La orden canónica es `./script/rc3_rc8_gate.sh`.
