# ADR-0033: Activación reproducible del modelo local

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** entrenar y dejar un modelo fuera del bundle no habilita la función al usuario.
2. **Eliminar:** no se añade wizard, updater, daemon privilegiado ni modelo descargado.
3. **Simplificar:** un script coordina las herramientas de entrenamiento e instalación existentes.
4. **Acelerar:** un modelo ya válido permite reanudar después de un fallo sin volver a entrenar.
5. **Automatizar:** preflight, modelo fuente, bundle instalado, firma y servicio se validan en orden.

## Decisión

`activate_wake_word.sh` acepta como máximo una ruta de dataset y usa por defecto el enrolamiento de
Application Support. Antes de entrenar ejecuta el modo `--check`, que aplica los mismos límites del
entrenador sin iniciar Create ML. El entrenador conserva su política de no sobrescritura.

Si el modelo fuente ya existe, el flujo exige `--validate-model` y continúa directamente con el
empaquetado Release. Después de instalar Jarvis vuelve a validar el modelo copiado dentro del bundle
y confirma que el LaunchAgent está activo. Cualquier fallo detiene la secuencia por
`set -euo pipefail`; no elimina el modelo ni el dataset, por lo que la ejecución puede reanudarse.

## Consecuencias

Completar 20+20 muestras y ejecutar un único comando deja el detector disponible en la Menu Bar. El
consentimiento de escucha continúa siendo una acción posterior y separada. La automatización escribe
solo en los destinos locales ya definidos y nunca transmite grabaciones o fingerprints.
