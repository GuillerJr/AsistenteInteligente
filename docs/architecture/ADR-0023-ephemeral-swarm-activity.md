# ADR-0023: actividad efímera del enjambre para el HUD

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** el grafo 3D no debe deducir actividad desde textos ni poseer estado de negocio.
2. **Eliminar:** no se añade WebSocket, event bus, persistencia, frontend ni dependencia JavaScript.
3. **Simplificar:** un contador en memoria por rol rodea únicamente llamadas reales a modelos.
4. **Acelerar:** `swarm.activity` reutiliza el UDS autenticado y el cliente Swift existente.
5. **Automatizar:** `try/finally` elimina actividad al completar, fallar o cancelar cada llamada.

## Decisión

`SwarmActivityTracker` mantiene contadores concurrentes para los siete valores de `AgentRole`. El
router, el especialista seleccionado y el sintetizador entran en un scope justo antes de llamar al
proveedor y salen siempre al finalizar. Memoria, autorización y herramientas no se presentan como
agentes de inferencia.

`swarm.activity` no acepta payload y devuelve una lista acotada de objetos `{role, active_jobs}`.
No contiene texto, modelos, argumentos, resultados, identificadores ni tiempo. El parser nativo
rechaza roles desconocidos, duplicados, más de siete entradas y contadores fuera de 1...128.

Siguiendo la frontera de Three.js, este contrato pertenece a la simulación/orquestación. La futura
esfera será un adaptador de render: mapeará roles a clústeres sin mutar ni reconstruir el estado del
daemon. La ventana continuará cerrada hasta una invocación explícita.

## Encaje en el roadmap

- **Fase 1:** instrumenta las transiciones reales de router, especialista y sintetizador.
- **Fase 5:** establece la entrada mínima y no sensible para la esfera de nodos 3D.

## Actualización

ADR-0069 conserva este payload agregado y sustituye su consulta periódica por `swarm.wait`, una
espera autenticada y versionada que también alimenta la presencia del notch.
