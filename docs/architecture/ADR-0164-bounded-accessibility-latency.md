# ADR-0164: latencia Accessibility acotada

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Evidencia

Las llamadas Accessibility son IPC síncrono con la aplicación observada. Una app bloqueada puede
responder tarde o devolver `kAXErrorCannotComplete`; el timeout exterior del helper evita una espera
infinita, pero consume casi todo el ciclo y degrada conversación, recaptura y cancelación.

La API pública `AXUIElementSetMessagingTimeout` permite fijar un timeout global para el proceso que
realiza las consultas. El SDK aclara que configurarlo en un elemento de aplicación no se propaga a
otros elementos AX equivalentes, por lo que debe aplicarse al objeto system-wide del helper.

## Decisión

1. Cada proceso efímero del helper configura el timeout AX global antes de usar Accessibility.
2. Percepción usa 150 ms por llamada y deja de iniciar consultas después de un presupuesto monotónico
   de 500 ms. Ventanas, elementos, profundidad e hijos conservan límites duros de 8, 256, 8 y 24.
3. Al agotar timeout o presupuesto, el árbol se marca `truncated`; OCR local puede continuar, pero la
   información AX parcial no se presenta como observación completa.
4. Las acciones usan un segundo por llamada para tolerar aplicaciones ocupadas sin alcanzar el
   timeout exterior del relay.
5. `AXPress` no se reintenta: Apple documenta que `cannotComplete` no prueba que la acción careciera
   de efecto. La sesión falla cerrada y una respuesta de bajo nivel nunca se toma como prueba de éxito.
6. No se añade hilo, timer, polling, proceso persistente, dependencia ni llamada NVIDIA.

## Consecuencia

Una aplicación no responsiva deja de monopolizar el ciclo visual. Las apps sanas conservan el mismo
recorrido y las apps lentas producen evidencia explícitamente incompleta en vez de latencia abierta;
el límite del relay continúa como última barrera absoluta.
