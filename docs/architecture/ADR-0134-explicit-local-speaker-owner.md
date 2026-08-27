# ADR-0134: propietario vocal local y explícito

- Estado: aceptado
- Fases: 2, 3 y 5

## Decisión

1. Un modelo con una sola etiqueta de hablante mantiene el propietario automático existente. Un
   modelo con varias etiquetas no obtiene propietario hasta que el usuario lo elige expresamente en
   la ventana de identidad de Jarvis.
2. La interfaz ofrece exclusivamente las etiquetas del modelo Core ML activo y validado. Cambiar o
   quitar la elección requiere confirmación visible y rota el `conversation_id` de voz vigente.
3. `UserDefaults` conserva solo el identificador seleccionado. Si no existe en el modelo activo, la
   política falla cerrada y no elige otro perfil por aproximación.
4. Cada transcript local añade `owner_speaker_profile`. El campo es verdadero únicamente cuando la
   voz reconocida coincide con el propietario resuelto. `sole_speaker_profile` se conserva como
   compatibilidad para el caso histórico de un solo perfil.
5. El daemon acepta cualquiera de las dos pruebas locales junto con el umbral de confianza ya
   vigente. Voz más imagen transporta ambos indicadores en su contexto compacto y autenticado.
6. Identidad y selección siguen sin autenticar, autorizar, aprobar herramientas o omitir una
   confirmación. Los logs no incluyen la etiqueta elegida.

## Motivo

Permitir varios perfiles era útil para reconocer interlocutores, pero hacía imposible conservar
memoria privada del propietario sin adivinar su identidad. Una selección explícita resuelve esa
ambigüedad con estado mínimo y reutiliza el clasificador, IPC, confirmaciones y almacenamiento local
existentes; no añade modelos, servicios, polling ni red.

## Consecuencias

- El propietario conserva conversación y adaptación privadas aunque el modelo reconozca invitados.
- Un invitado puede conversar de forma aislada, pero no hereda ni modifica el contexto privado.
- Un valor obsoleto o manipulado no cambia silenciosamente de propietario.
- Los clientes de diagnóstico que no proporcionan selección continúan fallando cerrados con modelos
  de varias voces.
