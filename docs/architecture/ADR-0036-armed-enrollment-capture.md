# ADR-0036: Preparación visible antes de cada muestra

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** abrir el micrófono al mismo instante del clic puede cortar el inicio de la palabra.
2. **Eliminar:** no se añade countdown animado, tono que contamine el clip ni sesión automática.
3. **Simplificar:** un estado visual de un segundo precede a la captura existente de dos segundos.
4. **Acelerar:** menos muestras truncadas reducen repeticiones y mejoran el primer dataset.
5. **Automatizar:** el estado bloquea acciones concurrentes y una cancelación impide abrir audio.

## Decisión

Tras pulsar `Grabar 2 s`, la UI entra en `arming` durante un segundo. En esta etapa no se construye
`WakeWordEnrollmentRecorder` ni se abre `AVAudioEngine`. Solo después cambia a `recording` y ejecuta
la misma captura acotada, control de calidad y confirmación privada del CAF.

No se reproduce un tono: su cola acústica podría contaminar especialmente la clase `background`.
La interfaz indica que el usuario debe hablar únicamente cuando aparezca el estado rojo `Grabando`.

## Consecuencias

Cada muestra conserva un único consentimiento explícito y un límite total previsible de tres
segundos. Cerrar o cancelar la tarea durante la preparación no inicia el micrófono ni crea archivos.
