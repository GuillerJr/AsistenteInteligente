# ADR-0022: monitor pasivo de integridad de auditoría

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** monitorear el host no autoriza a ejecutar herramientas críticas sin aprobación.
2. **Eliminar:** no se añade daemon, scheduler, notificación, base de datos ni framework.
3. **Simplificar:** se verifica la cadena hash existente y se publica un único estado agregado.
4. **Acelerar:** la Menu Bar reutiliza su sondeo autenticado de diez segundos.
5. **Automatizar:** una integridad ausente, inválida o comprometida bloquea nuevos turnos.

## Decisión

El daemon expone `security.status` sobre el UDS autenticado. El método no acepta payload y verifica
la cadena append-only mediante el `HashChainAuditLog` existente fuera del event loop. La respuesta
contiene exclusivamente `state=intact` o `state=compromised`; no expone cantidad de registros,
hashes, rutas ni causa concreta.

La Menu Bar consulta este método junto con `health` cada diez segundos. Muestra el estado agregado,
prioriza el escudo de alerta cuando existe compromiso y exige `intact` para iniciar voz. Unified
Logging usa la categoría `SecurityMonitor` y registra solo cambios de estado, nunca cada sondeo.

No se ejecutan inventarios de procesos, listeners ni red de forma periódica. Esas operaciones
continúan requiriendo aprobación humana exacta y de un solo uso.

## Encaje en el roadmap

- **Fase 4:** aporta monitoreo continuo y fail-closed sobre la evidencia de seguridad del daemon.
- **Fase 5:** integra el estado en la superficie voice-first sin abrir una ventana ni crear un chat.
