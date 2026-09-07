# ADR-0204: Calificación por flujos de producción

- Estado: aceptado
- Responsable del requisito: Guillermo / gzambrano27
- Bloque: P6

## Contexto

El benchmark de aceptación existente prueba 25 contratos aislados. Ese resultado no demuestra que
conversación, autorización, planificación, ejecución, verificación y recuperación mantengan su
causalidad cuando se encadenan. Ejecutar aplicaciones reales en cada commit tampoco es correcto:
depende de TCC, datos del usuario, disposición visual y estado energético del Mac.

## Decisión

Se añade `JarvisProductionWorkflowBenchmark`, un segundo gate de 20 flujos multiestado ejecutado
en fixtures privados y efímeros. Cada flujo declara checkpoints obligatorios; el runner falla si
omite o repite uno. El proceso bloquea AF_INET/AF_INET6, aplica cinco segundos de timeout por flujo
y publica solamente evidencia operacional acotada.

Los gates quedan separados por nivel de evidencia:

1. `acceptance-benchmark`: contratos unitarios locales, 25/25.
2. `production-workflows`: integración determinista multiestado, 20/20.
3. `macos-qualification`: hardware, TCC, procesos y socket del Mac real.
4. P7: resistencia prolongada, recursos acotados y disponibilidad del runtime instalado.
5. Evidencia física específica: compatibilidad por aplicación y comportamiento humano.

`production-workflows` es obligatorio en pre-push, harness evolutivo, beta diaria y calificación de
hardware. No se ejecuta en pre-commit para conservar un ciclo local corto.

La calificación física reutiliza exclusivamente `~/Applications/Jarvis.app`, exige que su
`AegisBuildRevision` coincida con `HEAD` y confirma que el PID pertenece a ese ejecutable. No mata
la instalación estable para lanzar una build efímera con una identidad TCC diferente.

## Consecuencias

- Un runner vacío ya no puede aprobar por ausencia de excepciones.
- Los resultados son comparables mediante la huella SHA-256 del manifest.
- Ningún flujo realiza correo, calendario, navegador o modificación real del sistema.
- P6 no convierte simulaciones en afirmaciones comerciales. P7 mide deriva, latencia y recursos
  acumulativos, mientras la evidencia física permanece en gates explícitos por hardware y por
  aplicación.
