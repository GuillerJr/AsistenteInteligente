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

## Investigación tras reiniciar el Mac

El reinicio del dueño conservó macOS 27.0.1 (26A434) y creó el proceso `dasd`
PID 404, que siguió cerca del 100 % de CPU. Las primeras cuatro muestras
directas de 45 s pasaron de 221 453 774 848 a 224 637 288 448 B libres. Esto
acredita recuperación de espacio durante ese intervalo, no la desaparición del
atasco de CPU.

Monitor de Actividad permitió obtener una muestra nativa sin leer bases de
datos: el hilo `com.apple.PerfPowerServicesReader.SQLite` aparece en las 1 757
muestras dentro de esta cadena:

```text
PPSRequestDispatcher dataForRequest:withError:
PPSSQLiteTimeSeriesIngester parseDataForRequest:outError:
PPSSQLiteQuery enumerateProperties:error:enumerationHandler:
PPSSQLiteDatabase _stepStatement:hasRow:error:
sqlite3_step → sqlite3VdbeExec / comparación de registros B-tree
```

El inspector de archivos abiertos y un `lsof` autenticado por el dueño vinculan
ese trabajo con la familia exacta:
`/private/var/db/powerlog/Library/PerfPowerTelemetry/BackgroundProcessing/CurrentBackgroundProcessingDB.BGSQL`,
su `-wal` y su `-shm`. Los dos únicos procesos registrados con archivos abiertos
en esa familia fueron `dasd` (404, lectura de DB/WAL) y `PerfPowerServices`
(409, lectura/escritura). Los tres archivos sumaban 19 962 512 B lógicos. No se
leyeron esquemas, filas, SQL de consultas ni datos personales de esa base.

La evidencia identifica el camino ocupado en PowerLog; **no demuestra
corrupción ni establece qué registros o consulta producen el atasco**. No se
ha borrado ni renombrado esta familia ni el almacén `DuetActivityScheduler`.

Evidencia local en `/private/tmp/jarvis-dasd-diagnostico-20261028/`:

- `dasd-404.sample.txt`: 159 244 B, SHA-256
  `33583b0c412e055574c432216cc7baf87706d5524628def537d582969497e2e1`.
- `powerlog-holders.raw`: metadatos de descriptores, SHA-256
  `a8e6f43667a7c4f73f9852f147149b7dcc5566a45f21652aaa78052fb110e9c9`.
- `powerlog-stat.txt` y `space-direct.txt`: identidades, tamaños y espacio.

El catálogo oficial ofrece macOS 27.2 (26B123), 6 067 473 KiB, con reinicio.
El dueño autorizó instalarla antes de intervenir manualmente el almacén. La
descarga con `softwareupdate --download` terminó al 100 % con código de salida
0. La preparación se mantuvo durante un intervalo en el 98,51 %; una muestra de
nuestro cliente mostró espera de respuesta XPC y el servicio UpdateBrain de Apple
seguía activo. No se repitió la descarga ni se lanzó otra en paralelo.

El intento de instalación con `sudo -n softwareupdate --install --no-scan`,
etiqueta exacta y usuario propietario, terminó antes de instalar con
`sudo: a password is required`. La autenticación e instalación/reinicio del
dueño y las mediciones posteriores siguen pendientes. El checkpoint local
`download-complete.json` conserva ese estado; no afirma que 27.2 esté instalada.
No se acepta una licencia automáticamente ni se fuerza el reinicio. Las notas
de Apple no acreditan una corrección específica de DASD/PowerLog:
[versión publicada](https://developer.apple.com/news/releases/?id=10262026d),
[cambios de macOS](https://support.apple.com/en-us/127257).

La reparación manual reversible queda sin ejecutar. Requeriría parar sus
servicios sin desactivar protecciones, confirmar ausencia de conexiones y
conservar la familia completa antes de permitir su regeneración. SQLite
[advierte sobre renombrar bases todavía abiertas](https://sqlite.org/howtocorrupt.html#unlinking_or_renaming_a_database_file_while_in_use).
La actualización autorizada no se presenta como solución comprobada hasta
observar su resultado en este Mac.

Durante la descarga, Monitor de Actividad mostró menos de 1 MB de escritura
acumulada del nuevo PID 404 y la familia PowerLog conservó sus tamaños e
identidades originales en las comprobaciones de metadatos. Esto distingue el
atasco de lectura actual del episodio anterior del temporal creciente. En ese
intervalo también hubo escritura de servicios Apple de extracción y assets;
sus contadores acumulados no equivalen al espacio asignado ni permiten atribuir
toda la caída del espacio libre a una sola causa. Las mediciones parciales del
actualizador no se presentan como inventario completo del almacenamiento.
