# ADR-0078: Entrenamiento nativo y acotado de identidad de voz

- Estado: aceptado
- Fases: 3 y 5

## Decisión

Cuando el dataset explícito alcanza el mínimo, Jarvis ofrece una sola acción para entrenar el modelo
de identidad local. La app ejecuta fuera del hilo principal el producto Swift
`jarvis-speaker-trainer`, incluido en `Contents/Helpers` y firmado antes que el bundle. Su ejecutable,
dataset y salida son rutas fijas construidas por la app; la interfaz no acepta shell, argumentos ni
modelos externos.

Durante Create ML se pausa el wake word y se bloquean nuevos turnos, grabaciones y mutaciones del
dataset. La operación es de una sola instancia y nunca sobrescribe un activo existente. El usuario
debe iniciarla explícitamente porque consume CPU y puede tardar varios minutos.

El modelo se conserva en
`~/Library/Application Support/Aegis/Models/JarvisSpeakerIdentity.mlmodelc`. Cada transcriptor nuevo
lo busca antes que el recurso del bundle, por lo que la activación no requiere reinstalar Jarvis. El
directorio padre debe ser real, propiedad del UID actual y `0700`; cualquier enlace, permiso abierto
o modelo incompatible falla cerrado.

## Límite de seguridad

El helper valida estructura, extensiones, duración, tamaño, número de muestras, etiquetas y calidad
de validación antes de copiar el modelo compilado a su destino definitivo. La app no transmite ni
copia el dataset, no registra audio y no usa red. La identidad resultante continúa siendo una señal
falible para personalización: nunca autentica ni aprueba acciones.

## Consecuencias

- El flujo normal ya no exige Terminal, portapapeles ni reinstalación.
- El coste de Create ML solo existe tras una acción visible y con un dataset completo.
- El CLI anterior se conserva como vía de diagnóstico y recuperación.
- El empaquetado añade un binario nativo pequeño, pero evita un daemon, framework o runtime nuevo.
