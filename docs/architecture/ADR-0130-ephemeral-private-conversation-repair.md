# ADR-0130: reparación conversacional privada y efímera

- Estado: aceptado
- Fases: 1, 2, 3 y 5

## Decisión

1. Un feedback `unhelpful` válido abre en RAM una ventana de reparación de dos minutos, ligada al
   mismo `conversation_id`.
2. El siguiente turno no dedicado a feedback consume la ventana de forma atómica al entrar en la
   cola. No hay temporizador, polling, persistencia ni reintento implícito tras cancelación.
3. Un feedback `helpful` válido cierra una ventana pendiente. Feedback ausente, ambiguo, cancelado o
   de voz no verificada no cambia este estado.
4. El historial continúa siendo el de conversación ya almacenado; la ventana no duplica ni guarda
   texto adicional.
5. Solo el prompt de Apple Intelligence cambia a política `repair`. Los mensajes y payloads de
   NVIDIA conservan la política clasificada a partir del turno actual y omiten la señal privada.
6. La reparación no modifica memoria social, perfil, estilo, routing, herramientas, permisos ni
   confirmaciones.

## Motivo

Medir una respuesta deficiente sin facilitar la corrección deja el ciclo incompleto. Una señal
local, breve y de un solo uso permite que Jarvis aproveche el contexto existente sin convertir un
fallo aislado en preferencia permanente ni revelar feedback privado al proveedor remoto.

## Límites

- La ventana desaparece al reiniciar el daemon y caduca perezosamente al llegar otro turno.
- Si el turno de reparación se cancela o falla, la señal se considera consumida; el usuario puede
  expresar de nuevo la corrección o emitir otro feedback explícito.
- Una solicitud directa de herramienta puede consumir la ventana sin producir texto generativo.
- El veredicto persistido para métricas sigue la política separada de ADR-0129.
