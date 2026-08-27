# ADR-0128: feedback explícito de estilo exclusivamente local

- Estado: aceptado
- Fases: 1, 2, 3 y 5

## Decisión

1. El perfil del propietario mantiene ranuras independientes para `verbosity`, `tone`, `follow_up`
   y `repetition`; actualizar una dimensión no borra las demás.
2. Solo un vocabulario explícito y determinista modifica esas ranuras. Preguntas y preferencias
   contradictorias para la misma dimensión no se aprenden.
3. Cada ajuste se persiste como preferencia local con evidencia explícita. En voz se exige el único
   perfil reconocido por el clasificador local con el umbral vigente.
4. El sistema convierte etiquetas cerradas en instrucciones fijas para el cerebro local. Nunca
   promueve el contenido libre de una memoria a instrucción de sistema.
5. El fallback NVIDIA recibe la solicitud minimizada, pero no recibe etiquetas, instrucciones de
   estilo, perfil o memoria. `Restablece tu estilo` elimina solo las cuatro ranuras de estilo.
6. Un turno que contiene feedback inequívoco se confirma mediante una respuesta determinista local.
   No invoca Apple Intelligence o NVIDIA; en voz falla cerrado si el propietario no está verificado.

## Motivo

Una sola preferencia genérica de comunicación perdía información: aprender naturalidad podía borrar
brevedad. Dimensiones pequeñas y ortogonales hacen la adaptación predecible, revocable y barata sin
otro modelo, framework o almacén.

## Límites

- El cambio se aplica desde el siguiente turno exitoso; no reescribe la respuesta que lo originó.
- El feedback no modifica permisos, routing, memoria social o política de seguridad.
- La calidez nunca permite afirmar humanidad, conciencia, exclusividad o dependencia emocional.
- La evaluación conversacional mide regresiones posteriores, pero no altera preferencias sola.
