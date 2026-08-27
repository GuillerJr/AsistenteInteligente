# ADR-0139: preflight de voz en una sola consulta autenticada

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. La app usa `runtime.preflight` antes de una captura de voz o pantalla. Una respuesta HMAC válida
   reúne estado del daemon, credencial NVIDIA, cerebro local e integridad de auditoría.
2. El daemon ejecuta en paralelo la consulta de Keychain, la disponibilidad del helper local y la
   verificación de la cadena de auditoría. La respuesta comienza solo cuando las tres terminan.
3. Un campo ausente, valor desconocido, fallo de autenticación, socket inseguro o auditoría no
   íntegra conserva el cierre seguro existente y bloquea el turno.
4. `health`, `provider.status` y `security.status` permanecen disponibles para diagnóstico y CLI,
   pero la app ya no los encadena en el recorrido de activación.
5. No se introduce caché de integridad, lease, polling, reintento, proceso ni dependencia. Cada
   turno sigue verificando el estado actual.

## Motivo

Abrir el micrófono esperaba tres conexiones IPC seriales y las dos comprobaciones del proveedor se
ejecutaban una detrás de otra. Esa secuencia no aportaba una frontera adicional: todos los mensajes
usaban el mismo socket, HMAC, UID y ventana temporal. Un snapshot único conserva las verificaciones
y reduce el camino crítico al máximo de su trabajo paralelo.

## Consecuencias

- Cada activación reemplaza tres viajes IPC por uno.
- La latencia depende de la comprobación más lenta, no de la suma de Keychain, helper y auditoría.
- Las herramientas de diagnóstico conservan sus contratos anteriores.
- Una app nueva requiere que el daemon nuevo exponga `runtime.preflight`; el instalador reinicia el
  daemon antes de reemplazar la app.
