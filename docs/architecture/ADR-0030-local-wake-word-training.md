# ADR-0030: Entrenamiento local y auditable del wake word

- Estado: aceptado
- Fecha: 2026-08-20

## Filtro de ingeniería

1. **Cuestionar:** voces sintéticas no demuestran que el detector reconozca al usuario real.
2. **Eliminar:** no se usa Python ML, nube, dataset descargado ni SDK de terceros.
3. **Simplificar:** Create ML entrena dos clases sobre Audio Feature Print con regresión logística.
4. **Acelerar:** un ejecutable SwiftPM valida, entrena, compila y verifica el modelo en una llamada.
5. **Automatizar:** dataset, calidad, fingerprint y destino se validan antes de instalar el activo.

## Decisión

`jarvis-wake-word-trainer` exige exactamente `jarvis/` y `background/`, con 20 a 500 clips por
clase. Solo acepta WAV, AIFF o CAF regulares, sin enlaces, de 0,4 a 3 segundos, hasta 5 MiB cada uno
y 512 MiB totales. La división de validación es determinista: 20 %, semilla 42. Un error de
clasificación superior al 25 % impide exportar el modelo.

El modelo usa transferencia con Audio Feature Print y regresión logística. El entrenador calcula un
SHA-256 reproducible del contenido, lo incluye en los metadatos, compila el `.mlmodel` y verifica con
SoundAnalysis que las únicas etiquetas sean `background` y `jarvis`.

El wrapper guarda el modelo derivado fuera de Git en
`~/Library/Application Support/Aegis/Models/JarvisWakeWord.mlmodelc`, dentro de un directorio `0700`.
No sobrescribe modelos existentes. El empaquetado puede copiar ese activo al bundle firmado.

## Consecuencias

El código de entrenamiento no se enlaza con la app Jarvis. Las grabaciones continúan fuera del
repositorio y su captura requerirá un paso explícito posterior. Hasta contar con ejemplos reales y
superar validación, la aplicación permanece en `modelo pendiente` y no escucha en segundo plano.
