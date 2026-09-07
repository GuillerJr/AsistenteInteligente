# ADR-0207: calificación aislada del driver Chrome CDP

- Estado: aceptada
- Responsable: Guillermo (`gzambrano27`)
- Bloque: P9

## Contexto

P8 demuestra la frontera genérica AppKit, Accessibility y ScreenCaptureKit, pero no prueba que el
motor DOM de un navegador real acepte las órdenes de Jarvis. Usar YouTube o el perfil cotidiano del
propietario como fixture convertiría una prueba de regresión en una operación externa, mutable y con
datos privados. Un servidor CDP simulado tampoco probaría compatibilidad con la versión instalada de
Chrome.

## Decisión

P9 arranca el ejecutable firmado de Google Chrome en modo headless con un perfil único bajo
`/private/tmp`, debugging ligado exclusivamente a `127.0.0.1:9222`, servicios de fondo desactivados y
un proxy local cerrado para impedir acceso externo. El daemon instalado abre el WebSocket CDP real,
valida que `Browser.getVersion` pertenezca a Chrome y carga mediante `Page.setDocumentContent` un DOM
fijo que solo vive en memoria.

La calificación exige descubrir la instalación y el proceso, validar el producto y versión de
protocolo, localizar los controles, reemplazar texto, pulsar el botón y observar el cambio lógico.
Antes y después consulta al helper nativo para demostrar que no cambió la aplicación frontal ni la
posición del puntero físico. El reporte firmado por IPC contiene solo resultados, latencias y la
versión principal; no contiene DOM, capturas, URLs, audio o transcripciones.

## Seguridad y ciclo de vida

- El gate se niega a reutilizar un listener previo en el puerto 9222.
- La instancia no comparte cookies, credenciales, historial ni Keychain con el perfil del usuario.
- HTTP, HTTPS y FTP se bloquean también dentro de la sesión CDP antes de cargar la fixture.
- El perfil y el proceso efímeros se destruyen incluso ante una interrupción.
- Cualquier respuesta CDP malformada, producto distinto de Chrome o transición no verificada falla
  cerrada.

## Consecuencias

P9 certifica el contrato DOM/CDP de la versión de Chrome instalada en este Mac sin tocar datos del
usuario ni Internet. No certifica YouTube, una sesión autenticada, extensiones, Safari, Arc o
Firefox; esas superficies cambian independientemente y necesitan contratos separados. La voz y la
biometría del propietario continúan diferidas al bloque final de voz.
