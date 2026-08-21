# ADR-0034: Control local de calidad del enrolamiento

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** contar archivos no garantiza que las muestras contengan una palabra utilizable.
2. **Eliminar:** no se añade transcripción, reconocimiento secundario ni análisis en la nube.
3. **Simplificar:** el medidor Accelerate existente aporta RMS y pico durante la escritura del CAF.
4. **Acelerar:** una muestra inválida se descarta y explica inmediatamente, antes de entrenar.
5. **Automatizar:** solo clips que superan duración, señal, saturación y almacenamiento se confirman.

## Decisión

Cada buffer del enrolamiento se analiza en memoria mientras `AVAudioFile` escribe el temporal. Ambas
clases rechazan cualquier pico de 0,999 o superior. La clase `jarvis` exige al menos 120 ms con RMS
de 0,008 o superior; `background` no tiene mínimo audible para conservar ejemplos de silencio real.

El clip conserva el requisito previo de 0,4 segundos analizados y solo se mueve al dataset después
de superar la compuerta. Un fallo elimina el temporal mediante el mismo `defer` y la interfaz muestra
si la causa fue voz débil, saturación, permiso, almacenamiento o captura.

## Consecuencias

El dataset generado por la app evita silencios positivos y saturación obvia sin interpretar palabras
ni persistir métricas. Los umbrales son conservadores y deberán calibrarse con grabaciones reales;
el entrenador mantiene su validación independiente para datasets aportados externamente.
