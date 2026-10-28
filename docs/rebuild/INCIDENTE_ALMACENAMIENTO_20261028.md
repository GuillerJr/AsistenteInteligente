# Incidente de almacenamiento — 28 de octubre de 2026

## Resultado observado

El llenado acelerado coincidió con un temporal SQLite `etilqs_*` abierto por
`/usr/libexec/dasd`, PID 594, del sistema Apple. La identidad Apple del ejecutable
se comprobó mediante su firma. No se leyeron contenidos del temporal ni de las
bases de datos del sistema.

Dos muestras de metadatos autenticadas por el dueño, separadas por 3,087765 s,
mostraron:

| Medida | Primera muestra | Segunda muestra | Variación |
| --- | ---: | ---: | ---: |
| Temporal lógico del mismo descriptor | 67 442 372 608 B | 67 830 120 448 B | +387 747 840 B |
| Espacio libre del volumen Data | 120 200 241 152 B | 119 814 213 632 B | −386 027 520 B |

El temporal crecía a 125,58 MB/s. Su crecimiento y la pérdida de espacio libre
coinciden en un 99,56 %. Esto identifica con mucha fuerza la causa inmediata del
consumo; no identifica la consulta ni el desencadenante del comportamiento.

## Intervención y comprobación

El dueño autorizó reiniciar únicamente `dasd`. Se eligió **Salir** en Monitor de
Actividad; tardó en terminar, pero finalmente el inspector mostró
`dasd (594) (terminado)`. **No se llegó a usar Forzar salida.** macOS inició otra
instancia, PID 22097. No se borraron archivos personales ni bases del sistema.

Cinco muestras directas posteriores, fuera de la vista restringida, abarcan
117,28 s: el espacio libre osciló 5 140 480 B y terminó en 187 603 476 480 B
(187,60 GB), con una variación neta de +1 794 048 B. El llenado acelerado cesó en
ese intervalo. La nueva instancia seguía cerca del 100 % de CPU: esto **no**
acredita una solución permanente de la causa de fondo.

Las primeras lecturas posteriores dentro del entorno restringido devolvían
valores fijos/redondeados. Se descartaron para afirmar estabilidad precisa y se
repitió la medición directamente. Los datos originales no se sobrescribieron.

## Relación con las pruebas

No había compiladores Swift, clang, xcodebuild ni suites de pruebas activos
durante el crecimiento sostenido observado. El último intento local de
compilación se detuvo con `global_storage_guard` a los 13,667 s: su volumen
propio alcanzó 103 165 952 B. La imagen propia retirada tras confirmar su
desmontaje medía 383 782 912 B. Esas cantidades no explican por sí mismas el
temporal de decenas de GB.

No se ha probado qué activó el trabajo de `dasd`. Las pruebas podrían haber
disparado indirectamente actividad del sistema; la coincidencia temporal no
permite afirmarlo ni descartarlo. La validación completa y las compilaciones
permanecen en CI remota. Se cerraron el cliente y el peer MCP sintéticos abiertos
durante la comprobación posterior; no se sustituyó la app personal.

## Evidencia local

En `/private/tmp/jarvis-mcp-native-qualification-20261028/`:

- `disk-samples.jsonl`: dos muestras previas, sólo metadatos.
- `disk-direct-after-restart.jsonl`: muestras directas posteriores válidas.
- `disk-after-restart.jsonl`: lecturas restringidas descartadas para precisión.
- `Diagnostico-Almacenamiento.command` y `disk-metadata-parser.py`: diagnóstico
  de sólo lectura ejecutado y autenticado por el dueño.

Los temporales pueden ser retirados por macOS; este documento conserva las
medidas relevantes. No acredita ausencia de otras causas de almacenamiento ni
el cierre de la cualificación funcional de MCP, plugins y skills.
