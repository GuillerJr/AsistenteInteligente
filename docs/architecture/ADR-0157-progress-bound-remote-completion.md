# ADR-0157: finalización remota ligada al progreso

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

Una cita `done` podía estar correctamente ligada a Accessibility y aun así describir una etiqueta
que ya existía antes de la acción. Un cambio visual distinto bastaba para que el modelo interpretara
éxito, aunque la evidencia citada no demostrara el efecto de esa acción.

## Decisión

1. Antes de cada acción remota, el controlador conserva el digest perceptual existente y un conjunto
   efímero de títulos y textos Accessibility no sensibles.
2. Después de actuar, `done` exige progreso según la compuerta perceptual existente y una evidencia
   exacta que no perteneciera al conjunto anterior.
3. El prompt publica `completion_evidence` con solo los candidatos locales nuevos. Si está vacío, el
   especialista no puede declarar finalización.
4. Una evidencia antigua termina como `blocked/uncertain_state`, aunque el dHash haya cambiado.
5. Esperar sin ejecutar una acción no crea artificialmente una línea base de mutación. La evidencia
   actual sigue siendo válida para objetivos puramente observacionales.
6. El estado se reemplaza en cada acción; no forma historial, no se persiste y no añade capturas,
   inferencias, permisos o dependencias.

## Consecuencia

La finalización posterior a una acción queda vinculada a su efecto observable. El cambio perceptual
y la evidencia nueva deben coincidir en el mismo paso, reduciendo falsos éxitos sin penalizar la
consulta de un estado que ya estaba completo antes de actuar.
