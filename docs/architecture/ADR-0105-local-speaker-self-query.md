# ADR-0105: consulta local de identidad del hablante

- Estado: aceptado
- Fases: 3 y 5

## Decisión

1. Una gramática cerrada reconoce preguntas exactas como «¿Quién soy?» o «¿Me reconoces?» después
   de la transcripción final on-device y antes de enviar el transcript mediante `voice.submit`. El
   preflight normal del turno permanece intacto.
2. La respuesta reutiliza exclusivamente el `speakerID` que el clasificador local existente ya
   aceptó con su evidencia, confianza y margen mínimos. No se crea otro umbral ni se consulta la
   huella de voz.
3. El identificador se vuelve a validar con el contrato de etiquetas existente y solo se sustituyen
   guiones por espacios para pronunciarlo. No se revela la confianza, la muestra ni el modelo.
4. Si el modelo está listo pero el turno no produjo identidad, Jarvis responde que no alcanzó
   confianza suficiente. Si el modelo no está listo, informa que falta configurarlo.
5. No se añade proceso, dependencia, red, modelo, persistencia, permiso, memoria, método IPC o
   sondeo. Los logs conservan únicamente estado de capacidad y resultado booleano, nunca identidad,
   transcript o confianza.

## Motivo

La transcripción ya contiene una decisión efímera del clasificador local. Reutilizarla ofrece una
respuesta personal inmediata sin duplicar análisis ni enviar información biométrica al daemon o a
NVIDIA.

## Límites

- La coincidencia es personalización probabilística; nunca autentica al propietario, concede
  capacidades, aprueba herramientas ni sustituye una confirmación.
- Jarvis pronuncia el identificador elegido al crear el perfil, no un nombre legal inferido.
- Una frase compuesta conserva el cerebro híbrido habitual.
- El identificador solo describe el turno actual y no se persiste como nueva evidencia.
