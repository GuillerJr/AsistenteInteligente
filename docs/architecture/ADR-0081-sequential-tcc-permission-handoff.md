# ADR-0081: Activación TCC secuencial y revalidación automática

- Estado: aceptado
- Fases: 3, 4 y 5

## Contexto

macOS puede mostrar un interruptor de Privacidad activado mientras el proceso que seguía vivo antes
del cambio todavía recibe un estado TCC anterior. Solicitar Pantalla y Accesibilidad al mismo tiempo
también puede superponer paneles y deja ambiguo qué identidad —Jarvis o su helper— necesita atención.

## Decisión

Jarvis solicita un solo permiso pendiente por vez. El flujo general avanza por Micrófono, Speech,
Pantalla y Control; dentro de Control, Pantalla precede a Accesibilidad. Las tarjetas conservan la
activación individual y el helper acepta argumentos separados para cada solicitud.
El estado y la solicitud de Micrófono usan `AVAudioApplication`, disponible para el mínimo macOS 14
del proyecto; no se mezclan con la autorización de captura de `AVCaptureDevice`.
Antes de pedir Micrófono, Speech, Pantalla o Control, la app `LSUIElement` se activa explícitamente
para que el diálogo de consentimiento no quede oculto cuando el LaunchAgent la abrió en segundo
plano. El helper de control aplica la misma regla.

Durante una solicitud, Jarvis refresca únicamente la capacidad implicada. Si el usuario entra en
Ajustes del Sistema, al abandonar esa aplicación Jarvis abre una nueva instancia firmada y termina
la anterior. La nueva instancia vuelve a consultar las APIs TCC; no infiere autorización a partir de
la interfaz de Ajustes. El relanzamiento solo se arma después de una acción explícita del usuario y
no ocurre durante el arranque normal. El proceso anterior solo termina si LaunchServices confirma
una instancia viva con un PID diferente.

El bundle declara solo los entitlements que TCC exige bajo hardened runtime para las capacidades ya
implementadas: `com.apple.security.device.audio-input` y
`com.apple.security.automation.apple-events`. No se modifica la base TCC ni se automatizan
interruptores de privacidad. La identidad estable `Jarvis Local Development` continúa preservando
el requisito designado entre builds locales.

## Consecuencias

- Las solicitudes de macOS no compiten entre sí.
- Un cambio que requiera reinicio se aplica sin pedir al usuario que cierre y abra Jarvis a mano.
- `permissions` cubre el recorrido completo y `computer-permissions` conserva el diagnóstico
  enfocado.
- Unified Logging registra únicamente estados agregados de los cuatro permisos, nunca contenido de
  voz, pantalla o acciones.
