# ADR-0009: Transcripción push-to-talk exclusivamente on-device

- Estado: aceptado
- Fecha: 2026-08-18

## Contexto

El asistente necesita convertir una intervención hablada en una solicitud del enjambre. Mantener un
micrófono o un reconocedor activo permanentemente ampliaría la superficie de privacidad y consumo.
Enviar audio a un servicio remoto tampoco es necesario para el primer flujo de voz.

## Decisión

`AegisAudioCore` integra Apple Speech mediante `SFSpeechAudioBufferRecognitionRequest`. El comando
`transcribe` es explícito, dura entre 1 y 60 segundos y configura obligatoriamente:

- `requiresOnDeviceRecognition = true`;
- resultados parciales para feedback local;
- puntuación local;
- un único tap de `AVAudioEngine` compartido con amplitud y VAD.

Antes de capturar se exige autorización de micrófono y Speech, locale soportado, recognizer
disponible y `supportsOnDeviceRecognition=true`. Si cualquiera falla, el helper termina cerrado y
emite un código estable. Nunca llama a `requestAuthorization`, nunca instala assets y nunca cambia
TCC. Esas acciones corresponderán a la futura aplicación Menu Bar firmada y a una decisión explícita
del usuario.

Cada transcript se normaliza a una línea, se limita a 4.096 caracteres y contiene UUID de captura,
secuencia, locale, duración, estado final y confianza opcional. No contiene PCM ni rutas. Los
parciales pueden alimentar feedback visual, pero solo un evento final con `on_device=true` satisface
el contrato Python.

El método autenticado `voice.submit` convierte el transcript final en un `UserRequest` con
modalidades `audio` y `text`. Reutiliza el filtro de secretos previo al proveedor, límites de jobs,
cancelación y continuidad conversacional. El atributo `on_device` es una afirmación del cliente
local autenticado; la garantía real proviene del helper que fuerza la opción Apple correspondiente.

## Estado del host

En la validación del 2026-08-18, macOS reporta permisos de micrófono y Speech `denied`. Los locales
`es-US` y `es-ES` son reconocidos, pero no hay asset on-device disponible. No se solicitó permiso ni
se inició descarga, por lo que no fue posible realizar una captura real en este hito.

## Consecuencias

El pipeline y sus fallos están listos sin habilitar escucha. La aplicación Menu Bar deberá incluir
`NSMicrophoneUsageDescription` y `NSSpeechRecognitionUsageDescription`, solicitar ambos permisos en
primer plano y publicar únicamente el transcript final mediante IPC autenticado. La conexión nativa
al UDS será el siguiente límite de integración.
