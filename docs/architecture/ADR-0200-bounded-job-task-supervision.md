# ADR-0200: Supervisión acotada de tareas y cierre de P2

- Estado: aceptado
- Fecha: 2026-09-04
- Prioridad: P2 — consolidación arquitectónica
- Responsable físico: Guillermo (`gzambrano27`)

## Contexto

El runtime de trabajos ya había separado contratos, estado, invocación del grafo,
presentación de herramientas, transporte IPC, admisión, ejecución, ciclo de vida,
autorización, fallos y finalización. Quedaba una fuga de responsabilidades: cada
`MutableJob` conservaba un `asyncio.Task`. Esto mezclaba el estado observable del
dominio con el mecanismo usado para ejecutarlo y duplicaba la creación, reemplazo,
cancelación y drenaje de tareas dentro de `SwarmJobManager`.

La mezcla también dejaba dos riesgos concretos:

1. una aprobación podía competir con `close()` y crear ejecución nueva durante el
   apagado;
2. una autorización rechazada podía retener indefinidamente la referencia a la
   tarea ya terminada.

El manifiesto SwiftPM, además, resolvía cuatro paquetes MLX grandes incluso cuando
`AEGIS_BUILD_MLX` estaba desactivado. Esa resolución no aportaba nada al runtime
nativo predeterminado y alargaba innecesariamente el ciclo de compilación.

## Decisión

`JobTaskSupervisor` es el único propietario de las tareas asíncronas de trabajos.
Mantiene dos colecciones acotadas:

- `current`: una única tarea cancelable por identificador de trabajo;
- `running`: todas las tareas que todavía deben ser drenadas, incluida la breve
  superposición entre el retorno del grafo y la ejecución aprobada.

El estado de dominio ya no contiene objetos `asyncio.Task`. Todo camino terminal
—éxito, fallo, cancelación, expiración o rechazo de autorización— elimina su
referencia actual. `close()` sella admisión y aprobación bajo el mismo bloqueo,
publica primero los estados terminales, cancela todas las tareas y sólo entonces
limpia el supervisor. Una finalización atómica ya iniciada conserva su regla de
linealización: puede completar antes de que la cancelación sea publicada.

Las dependencias MLX se declaran únicamente cuando `AEGIS_BUILD_MLX=1`. El build
normal mantiene ONNX Runtime, pero deja de resolver o advertir sobre paquetes que
ningún target activo consume.

## Invariantes de aceptación

1. No hay referencias a tareas dentro de `MutableJob`, `JobRegistry` ni
   `JobLifecycleCoordinator`.
2. Nunca existen dos tareas **actuales** para un mismo trabajo; una reemplazada
   puede drenar sin dejar de estar supervisada.
3. Un cierre iniciado rechaza nuevas admisiones y aprobaciones.
4. Toda tarea activa se cancela y drena antes de liberar el supervisor.
5. Los errores escapados registran sólo el tipo y el nombre de tarea; nunca el
   texto que podría contener secretos de proveedor o herramienta.
6. El build Swift predeterminado no incorpora dependencias MLX inactivas.

## Consecuencias

P2 queda cerrado como consolidación de fronteras del runtime. El siguiente ciclo
P3 puede concentrarse en fiabilidad observable del producto y pruebas reales de
interacción, sin seguir ampliando el coordinador de trabajos salvo para corregir
un defecto demostrado por una prueba.
