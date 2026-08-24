# ADR-0073: Enrolamiento privado y explícito de hablantes

- Estado: aceptado
- Fases: 3 y 5

## Decisión

Jarvis ofrece una ventana auxiliar singleton desde el Menu Bar para recolectar el dataset local de
identidad. El usuario crea entre dos y ocho identificadores seguros y graba explícitamente clips CAF
de tres segundos para cada persona y para `background`. Cada captura tiene un segundo de armado
visible y pausa la detección de la palabra de activación para mantener un solo propietario del
micrófono.

La palabra de activación y la identidad comparten una única primitiva nativa de captura y control de
calidad. No se incorpora otro framework, daemon, stream continuo ni servicio de red. La interfaz solo
recolecta y valida; el entrenamiento permanece en el comando local existente porque Create ML no debe
residir ni ejecutarse silenciosamente dentro de la app de fondo.

## Límite de seguridad

El dataset vive en `~/Library/Application Support/Aegis/SpeakerEnrollment`: directorios `0700`, clips
`0600`, máximo ocho perfiles, 500 clips y 5 MiB por clase/archivo respectivamente. Se rechazan
identificadores de ruta, enlaces simbólicos, permisos abiertos y contenido inesperado. Borrar un
perfil o las muestras requiere una confirmación visible y no altera modelos ya entrenados.

La identidad resultante sigue siendo una pista falible de personalización; nunca autentica, concede
capacidades ni aprueba acciones.

## Consecuencias

- El enrolamiento ya no exige construir manualmente la jerarquía del dataset.
- El notch conserva exclusivamente la presencia viva y automática de Jarvis.
- El Menu Bar mantiene una acción corta; el flujo profundo vive en su propia ventana.
- Entrenar y activar sigue siendo una acción explícita y auditable desde la raíz del proyecto.
