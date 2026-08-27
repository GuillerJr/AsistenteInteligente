# ADR-0126: diálogo social y continuidad relacional local

- Estado: aceptado
- Fases: 1, 2 y 3

## Decisión

1. Un clasificador determinista local asigna cada turno a `task`, `conversation`, `support`,
   `advice`, `brainstorm` o `repair`. La etiqueta controla tono, longitud y seguimiento; no es una
   evaluación psicológica.
2. Toda respuesta cálida conserva transparencia: Jarvis no afirma ser humano o consciente, no
   fomenta exclusividad y no se presenta como sustituto de relaciones personales.
3. La memoria relacional solo extrae declaraciones explícitas sobre un tema activo o un compromiso
   que el dueño pide recordar. Los temas caducan a los 90 días y los compromisos a los 30.
4. La voz solo puede enseñar esa memoria cuando coincide con el único perfil local verificado. El
   texto mantiene el supuesto existente de sesión local del propietario.
5. El dueño puede resolver, olvidar o reiniciar categorías mediante frases exactas. No existe
   promoción silenciosa de inferencias ni perfil emocional persistente.
6. Como máximo cuatro extractos relacionales se entregan al cerebro local como datos no confiables.
   NVIDIA no recibe historial, memoria relacional, perfil o identidad del hablante.

## Motivo

La sensación de continuidad depende más de entender el tipo de intercambio y recordar asuntos
relevantes que de generar respuestas largas. Reglas pequeñas y explícitas mejoran consistencia sin
otra inferencia, dependencia o frontera de datos.

## Límites

- Esta capa no diagnostica emociones, salud mental o intención clínica.
- Un recuerdo nunca autentica al hablante, autoriza herramientas o elimina confirmaciones.
- Los compromisos no sustituyen Recordatorios; son contexto conversacional, no alarmas.
- Solo se aprende después de que el job termina correctamente.
