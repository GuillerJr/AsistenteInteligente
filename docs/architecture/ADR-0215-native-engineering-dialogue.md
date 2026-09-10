# ADR-0215: conversación nativa por petición y evidencia bajo demanda

Estado: implementado; calidad semántica del modelo todavía no certificada.

## Problema

La solución anterior añadía una gramática de peticiones iniciales sin objetivo. No generalizaba
a fragmentos, correcciones o cambios de tema. Además, el helper persistente reutilizaba
`LanguageModelSession` por igualdad de instrucciones mientras Python reenviaba el historial:
duplicaba turnos y permitía contaminación entre conversaciones independientes.

Un inventario vacío en cada prompt también inducía al AFM a convertir una petición ambigua
en una auditoría, incluso después de mejorar las instrucciones.

## Decisión

- Eliminar esa gramática. Conservar solo los comandos deterministas existentes fuera de ella.
- Separar instrucciones confiables, referencias no confiables, historial y petición actual.
- Negociar protocolo del helper 2.1 con compatibilidad del cliente para 2.0 y one-shot.
- Representar historial con `Transcript.prompt/response`, nunca analizar roles escritos dentro
  de cadenas del usuario. La petición actual no se duplica en el historial.
- Crear un helper/sesión por transacción dentro del proceso persistente; sin conversaciones
  ocultas ni persistencia adicional. El daemon es el único propietario del historial.
- En ingeniería Apple, usar `GenerationSchema` con `needsRepository`, `needsClarification`
  y `response`. El primer paso ve solo el diálogo. Si requiere evidencia existente, una segunda
  generación recibe inventario/referencias. Máximo dos generaciones, sin auto-reintentos.
- Validar la presentación de aclaraciones antes de emitirlas. Un snapshot vacío solo indica
  progreso del transporte; no dispara delta de texto ni métrica de primera respuesta visible.

No se cambia el broker, se activan herramientas nuevas, se descargan modelos ni se envía
historial privado al proveedor remoto. La selección de contexto del modelo no concede permisos.
Los límites de 24 KiB y cancelación/estado térmico siguen vigentes.

API contrastada con el SDK instalado y la documentación oficial de
[Transcript](https://developer.apple.com/documentation/foundationmodels/transcript) y
[LanguageModelSession](https://developer.apple.com/documentation/FoundationModels/LanguageModelSession).

## Costes y límites

Se renuncia al KV-cache conversacional implícito para no mezclar sesiones. La consulta del
repositorio puede necesitar dos inferencias; hablar de un objetivo nuevo necesita una.
`GenerationSchema` asegura estructura, no comprensión ni veracidad. El selector de contexto
también puede equivocarse. El motor AFM no queda certificado como ingeniero generalista.

Los ensayos probabilísticos son optativos y separados del gate determinista: una prueba de
formato o transporte aprobada no demuestra calidad semántica.
