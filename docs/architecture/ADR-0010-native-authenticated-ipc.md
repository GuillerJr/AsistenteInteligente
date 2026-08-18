# ADR-0010: Cliente IPC nativo autenticado

- Estado: aceptado
- Fecha: 2026-08-18

## Contexto

El helper de audio y la futura aplicación Menu Bar deben enviar eventos al daemon Python sin abrir
un puerto TCP ni duplicar una frontera de confianza. ADR-0002 define un UDS privado y autenticación
HMAC, pero hasta ahora solo existía un cliente Python.

## Decisión

`AegisAudioCore` implementa el protocolo IPC `1.0` en Swift nativo:

1. Rechaza rutas relativas, sockets que no pertenezcan al UID efectivo y cualquier permiso para
   grupo u otros.
2. Limita cada frame NDJSON a 64 KiB, usa una solicitud por conexión y aplica timeouts de cinco
   segundos a lectura, escritura y conexión.
3. Genera UUID y nonce criptográfico de 128 bits por solicitud. El timestamp UTC y el JSON canónico
   son compatibles con la implementación Python.
4. Firma con HMAC-SHA256 y verifica que la respuesta esté ligada al UUID y nonce enviados, tenga un
   timestamp fresco y una firma válida. Ningún payload se publica antes de completar estas pruebas.
5. Recupera el secreto IPC de 256 bits desde macOS Keychain. El helper SwiftPM sin firma usa
   `/usr/bin/security` mediante `Process`, sin shell, con stdout privado, stderr descartado, salida
   máxima de 65 bytes y timeout. No acepta el secreto por CLI, entorno o stdin.
6. `transcribe-submit` comprueba primero `health`, ejecuta Apple Speech exclusivamente on-device y
   envía solo el transcript final a `voice.submit`. `submit-transcript` ofrece una frontera stdin
   para integración y pruebas: un solo JSON de hasta 16 KiB, esquema exacto y sin audio.

La consulta directa con `SecItemCopyMatching` se pospone hasta disponer de una app bundle firmada.
En el helper SwiftPM actual puede abrir un flujo ACL interactivo y bloquear el proceso, algo
inaceptable para un daemon o comando en segundo plano.

## Compatibilidad y validación

Un vector fijo generado por Python prueba byte por byte el orden JSON y HMAC en Swift. Una prueba de
integración crea una credencial Keychain temporal aislada, levanta un daemon real, ejecuta el helper
arm64, envía `voice.submit`, verifica la respuesta autenticada y elimina la credencial en `finally`.

El Command Line Tools instalado combina un compilador Swift `6.4.0.30.4` con el SDK 27 de
`6.4.0.31.4`. Para la validación reproducible de este hito se seleccionó explícitamente el SDK 26.5
incluido y se mantuvo el deployment target macOS 14. No se instaló la actualización beta completa
de macOS ofrecida por `softwareupdate`.

## Consecuencias

- Conocer la ruta del socket o controlar stdin no permite falsificar una solicitud.
- Un transcript parcial, remoto, sobredimensionado o con campos adicionales falla antes del socket.
- La futura Menu Bar App puede reutilizar `AegisAudioCore` y sustituir solamente el proveedor de
  credenciales cuando disponga de firma y access group definitivos.
- La concesión de permisos TCC y la instalación de assets de Apple Speech siguen fuera del helper y
  requieren una acción explícita del usuario.
