# ADR-0133: contexto privado de voz ligado al hablante

- Estado: aceptado
- Fases: 2, 3 y 5

## Decisión

1. Cuando el clasificador local dispone de un único perfil reconocido, la app liga el
   `conversation_id` de voz a su identificador acotado durante la ventana de 30 minutos definida en
   ADR-0132.
2. Solo el mismo perfil puede reutilizar o rotar explícitamente esa sesión. Una voz no verificada,
   varios perfiles enrolados o un clasificador no disponible producen un turno aislado y no
   reemplazan el UUID privado ya guardado.
3. El daemon aplica una segunda frontera: una solicitud con modalidad de audio no verificada no
   recibe historial, memoria RAG, perfil ni contexto relacional. Tampoco puede reutilizar una
   conversación que ya contiene turnos.
4. Una orden hablada con imagen transporta por IPC autenticado un `voice_context` compacto y se
   marca como audio más imagen. El contexto omite el texto duplicado, audio, embeddings y
   probabilidades internas; el UUID de captura conserva la defensa contra replay.
5. `UserDefaults` guarda únicamente UUID de conversación, timestamp e identificador validado. Los
   logs registran la causa operativa sin identificador, transcript ni confianza.
6. La identidad vocal continúa siendo una pista falible. No autentica al usuario, no autoriza
   herramientas, no aprueba acciones y no elimina ninguna confirmación.

## Motivo

Limitar la sesión por tiempo evita contexto obsoleto, pero no impide que otra persona herede una
conversación privada mientras el UUID siga vigente. La defensa coordinada en cliente y daemon evita
tanto exposición accidental como reutilización directa del IPC, sin otro servicio, modelo, polling
o dependencia.

## Consecuencias

- La conversación del propietario conserva continuidad y una voz no reconocida todavía puede
  recibir una respuesta aislada sin acceso a contexto privado.
- Con varios perfiles no se adivina cuál es el propietario; los turnos de voz fallan cerrados para
  memoria privada hasta una selección explícita. ADR-0134 incorpora esa selección.
- Las solicitudes textuales mantienen el supuesto existente de sesión local del usuario.
- NVIDIA continúa recibiendo únicamente la solicitud actual minimizada conforme a ADR-0123.
