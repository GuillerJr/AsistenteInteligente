# RC-4 — Presupuesto del bundle y runtime

## Línea base

El bundle instalado antes de RC-4 ocupaba aproximadamente 470 MiB. El análisis mostró que el mayor
desperdicio reparable sin degradar funciones estaba en símbolos locales repetidos dentro de los
ejecutables Swift Release.

## Cambio

- Los ejecutables Swift se procesan con `strip -x` después de copiarlos al bundle y antes de
  firmarlos.
- Cada ejecutable Swift queda limitado a 24 MiB.
- El bundle completo queda limitado a 440 MiB.
- La firma se aplica después del stripping; por tanto, no se invalida una firma ya emitida.
- MLX Whisper, SciPy y Numba se conservan porque todavía forman la ruta semántica de autorización
  por voz. Retirarlos habría convertido una reducción de tamaño en una regresión silenciosa.

## Evidencia local

El primer bundle completo posterior al cambio ocupó 406 028 KiB, frente a los ~470 MiB iniciales:
una reducción superior al 10 % sin quitar capacidades. El binario más grande quedó en 23 502 512
bytes, por debajo del límite de 25 165 824 bytes.
