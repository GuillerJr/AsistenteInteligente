# ADR-0076: Presencia visual coherente con el proveedor

- Estado: aceptado
- Fase: 5

## Decisión

Menu Bar, notch y HUD consumen el mismo `ProviderReadinessState`. Si la credencial NVIDIA falta o
Keychain no puede comprobarse, las tres superficies usan ámbar y describen la causa sin exponer
cuentas, servicios, errores internos ni valores secretos.

El sondeo periódico conserva los últimos estados conocidos de conexión, integridad y proveedor
mientras consulta el daemon. `checking` se usa solo durante el arranque, cuando todavía no existe
un resultado. Esto evita que la voz se deshabilite y que la interfaz parpadee brevemente en cada
ciclo de diez segundos. Las operaciones siguen validándose mediante el IPC autenticado, por lo que
un daemon que desaparezca durante ese breve intervalo falla de forma cerrada.
Una compuerta explícita mantiene el sondeo en modalidad single-flight y alimenta el indicador de
progreso del botón manual; conservar el último estado no permite lanzar comprobaciones paralelas.

El notch no convierte una indisponibilidad persistente en estado prominente: mantiene sus 10 fps de
reposo. El HUD reutiliza sus animaciones y el monitor existentes. No se añaden timers, tareas,
sensores ni llamadas IPC.

## Consecuencias

- Jarvis no aparenta disponibilidad fuera de Menu Bar cuando el enjambre no puede usar NVIDIA.
- La información visible sigue siendo operacional y no secreta.
- Un fallo persistente del proveedor no aumenta el consumo gráfico en segundo plano.
