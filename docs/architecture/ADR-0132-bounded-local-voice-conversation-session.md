# ADR-0132: sesión de conversación de voz local y acotada

- Estado: aceptado
- Fases: 2, 3 y 5

## Decisión

1. La app macOS reutiliza su `conversation_id` durante 30 minutos desde el último turno aceptado por
   el daemon.
2. Antes de cada envío comprueba localmente UUID, última actividad y reloj. Estado incompleto,
   antigüedad igual o superior al límite, reloj retrocedido o timeout inválido producen `nil`; el
   cliente crea entonces otra conversación mediante el flujo IPC existente.
3. La política no usa temporizador, polling, modelo ni red adicional. `UserDefaults` contiene solo
   UUID y timestamp de última actividad.
4. Frases exactas como `Jarvis, nueva conversación` limpian esos dos valores y responden mediante la
   voz local. Frases compuestas o ambiguas continúan hacia el cerebro normal.
5. Rotar una sesión no ejecuta `conversations.delete`: no borra historial, memoria, perfil, temas ni
   compromisos.

## Motivo

Conservar indefinidamente el mismo UUID mezcla temas separados por horas o días y hace que una
activación aparentemente nueva herede supuestos antiguos. Un límite comprobado al enviar elimina
esa contaminación sin mantener procesos despiertos ni perder continuidad durante un diálogo real.

## Límites

- La duración es fija para evitar otra preferencia y superficie de configuración.
- Una actualización desde versiones que no guardaban timestamp rota la conversación en el primer
  turno; no intenta adivinar la antigüedad.
- El historial anterior continúa ocupando su capacidad normal hasta un borrado explícito por la API
  de conversaciones.
- Las sesiones de CLI o clientes distintos conservan su propia política de `conversation_id`.
