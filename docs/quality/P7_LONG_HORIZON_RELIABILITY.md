# P7 — Resistencia prolongada y runtime instalado

P7 responde una pregunta concreta: ¿los contratos que ya aprobaron P6 continúan correctos cuando
se repiten, sin deriva de estado, crecimiento de memoria, tareas asíncronas abandonadas ni actividad
de red? No intenta sustituir las pruebas físicas de voz ni afirmar que cada versión de una aplicación
externa sea compatible.

## Compuerta

```bash
./script/p7_reliability_gate.sh
```

La compuerta falla cerrada salvo que se cumplan todas estas condiciones:

1. `~/Applications/Jarvis.app` es un bundle real y no un enlace simbólico.
2. `AegisBuildRevision` coincide exactamente con `git rev-parse HEAD`.
3. La firma, entitlements, arquitectura arm64, LaunchAgents, socket privado, diagnóstico y snapshot
   de capacidades aprueban `jarvis_beta.sh check`.
4. El benchmark prolongado completa 20 ciclos medidos de los 20 flujos P6.
5. El daemon conserva identidad, seguridad y recursos acotados durante 100 ciclos IPC.

Si el equipo está en Low Power Mode o suspensión térmica, el soak del daemon usa únicamente
superficies de estado seguras. En modo activo también confirma que no existan agentes ocupados antes
de medir. Esto evita tanto despertar trabajo especulativo como declarar un falso positivo con cero
ciclos ejecutados.

El soak valida además una ronda IPC completa de calentamiento antes de fijar la línea base de RSS,
CPU e hilos. De este modo una muestra concurrente temprana de `runtime.metrics` no atribuye a una
fuga las páginas que sus cuatro handlers hermanos cargan una sola vez. Las 100 rondas posteriores
continúan sujetas al límite estricto de 8 MiB.

El preflight no precarga MLX Whisper: esa optimización retenía decenas de MiB aunque no existiera una
confirmación vocal y contaminaba la línea base del soak. El modelo se carga únicamente ante una
autorización vocal explícita y puede permanecer en la caché nativa para el resto de esa sesión.

## Contrato matemático y operacional

- Carga medida: `20 × 20 = 400` flujos.
- Checkpoints medidos: `20 × 58 = 1160`.
- Calentamiento: un ciclo P6 completo y verificado, excluido del conteo y de la línea base RSS.
- Presupuesto de latencia: p95 por ciclo menor o igual a 2.000 ms.
- Presupuesto de memoria: crecimiento máximo de RSS menor o igual a 8 MiB después del calentamiento.
- Concurrencia: cero tareas `asyncio` pendientes creadas por el benchmark.
- Privacidad: cero AF_INET/AF_INET6; el reporte no contiene prompts, URLs, audio, imágenes ni
  transcripciones.

La línea base local de cierre registró `400/400`, `1160/1160`, p95 de 230 ms, máximo de 231 ms,
crecimiento RSS máximo de 3.358.720 bytes, cero tareas filtradas y cero intentos de red. Estos valores
son evidencia de esa ejecución; los presupuestos, no los tiempos exactos, constituyen el contrato.

## Voz del propietario: diferida deliberadamente

P7 no lee muestras biométricas, no genera distractores y no abre una sesión de grabación. El harness
evolutivo mantiene esa prueba aislada y solo la habilita con consentimiento explícito:

```bash
AEGIS_RUN_OWNER_VOICE_QUALIFICATION=1 ./script/jarvis_evolutionary_test_harness.py
```

Hasta el bloque final de voz, el reporte usa `owner_voice_qualification=deferred_until_voice_final`.
Las pruebas sintéticas del protocolo de voz pueden continuar porque no capturan ni imitan la voz del
propietario.

## Qué no demuestra P7

- Precisión real del wake word o de la identidad vocal de Guillermo.
- Éxito contra todas las versiones, extensiones, idiomas o disposiciones de Chrome, Safari, Mail,
  Finder y Xcode.
- Disponibilidad de NVIDIA NIM u otros servicios externos.
- Notarización pública sin una cuenta válida de Apple Developer.
- Ausencia absoluta de defectos: demuestra cumplimiento repetido de los contratos cubiertos.

La compatibilidad de una aplicación se incorporará únicamente con un escenario no destructivo,
reproducible, versionado y con verificación posterior a la acción. Las afirmaciones comerciales deben
citar esa evidencia específica, no extrapolar el resultado `400/400`.
