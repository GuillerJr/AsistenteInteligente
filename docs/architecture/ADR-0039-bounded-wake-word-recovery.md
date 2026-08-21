# ADR-0039: Recuperación acotada del micrófono

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** `AVAudioEngine` se detiene cuando cambia el dispositivo, pero conservar la
   referencia al objeto puede hacer que Jarvis parezca escuchar aunque el stream terminó.
2. **Eliminar:** no se añade monitor de dispositivos, polling, daemon auxiliar ni backoff genérico.
3. **Simplificar:** la notificación nativa del engine marca el fallo y consume un solo reintento.
4. **Acelerar:** dos segundos permiten que macOS estabilice la nueva entrada sin interacción.
5. **Automatizar:** 30 segundos de escucha estable restauran el único cupo de recuperación.

## Decisión

`WakeWordDetector` observa `AVAudioEngineConfigurationChange` únicamente durante un stream activo.
El callback no destruye el engine en la cola interna de audio: entrega el fallo al actor principal,
que ejecuta la parada segura.

`WakeWordRecoveryGate` concede un solo reintento. La Menu Bar espera dos segundos antes de volver a
validar permisos, modelo y formato. Un segundo fallo permanece visible y no crea otro ciclo. Tras 30
segundos continuos con el detector ejecutándose, el cupo vuelve a estar disponible.

Desactivar manualmente la escucha cancela recuperación y estabilidad. Pausar por un turno de voz
cancela únicamente la estabilidad y conserva el límite defensivo.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** tolera cambios reales de entrada en macOS.
- **Fase 5 — Voice-first:** recupera el daemon auditivamente sin abrir una ventana.

## Consecuencia

Conectar o retirar un micrófono deja de producir una falsa escucha silenciosa. Los fallos
persistentes quedan cerrados después de un intento y requieren una acción explícita del usuario.
