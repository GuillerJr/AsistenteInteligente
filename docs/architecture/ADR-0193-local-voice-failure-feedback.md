# ADR-0193: feedback local de fallos de voz

## Estado

Aceptada.

## Contexto

Los fallos anteriores a `voice.submit` dejaban el notch en un estado fallido sin explicar al usuario
si faltaba un permiso, no se detectó voz o el runtime local no estaba listo. Además, una aprobación
pendiente podía coexistir con el inicio de otro turno y su ventana permanecía abierta al caducar.

## Decisión

1. Una tabla pura y acotada traduce fallos de preflight, captura y job a frases seguras en español.
2. Las cancelaciones intencionales (`cancelled`, `job_cancelled` y `job_superseded`) son silenciosas.
3. El feedback anterior al submit usa síntesis local: no crea job, conversación ni solicitud NVIDIA.
4. Voz, imagen y pantalla rechazan un turno nuevo mientras exista una aprobación pendiente.
5. La vista SwiftUI observa la transición de aprobación existente a `nil` y cierra su propia ventana
   con `dismissWindow`; no añade estado global ni un puente AppKit.

## Consecuencias

El usuario recibe una instrucción inmediata y no técnica ante fallos recuperables. Las interrupciones
siguen siendo naturales, una aprobación conserva exclusividad y su ventana ya no queda obsoleta. La
tabla pura puede probarse sin micrófono, permisos, red ni UI.
