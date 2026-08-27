# ADR-0120: autonomía local por eventos y notificaciones proactivas

## Estado

Aceptado.

## Decisión

1. La función es opt-in y persiste solo un booleano en `UserDefaults`. Habilitarla exige autorización
   de notificaciones; denegarla deja el monitor apagado.
2. Red usa `NWPathMonitor`; batería usa `IOPSNotificationCreateRunLoopSource`; memoria usa
   `DispatchSourceMemoryPressure`; temperatura usa la notificación de `ProcessInfo`; calendario usa
   `EKEventStoreChanged`.
3. No hay ciclo de polling. EventKit programa un único temporizador para el próximo aviso dentro de
   siete días y, cuando no existe, una única revaluación diaria.
4. Batería avisa al 20 % y 10 % solo al descargar. Red avisa únicamente en transiciones. Memoria y
   temperatura avisan solo en estados elevados.
5. Una compuerta local limita a 128 claves recientes y aplica enfriamiento. El calendario conserva
   como máximo 512 IDs avisados durante la sesión para impedir ciclos y duplicados.
6. Las notificaciones son locales, no ejecutan acciones y omiten títulos o contenido del calendario.

## Consecuencias

- Jarvis consume CPU solo cuando macOS entrega un evento o vence el único temporizador de agenda.
- Calendario requiere `NSCalendarsFullAccessUsageDescription` y una decisión TCC independiente.
- Desactivar la función cancela monitores y temporizadores, elimina observadores y reinicia la
  deduplicación en memoria.
