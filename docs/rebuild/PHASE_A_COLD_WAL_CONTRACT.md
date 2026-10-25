# Contrato de fase A: captura SQLite fría con WAL

**Estado: primer incremento implementado y probado con datos sintéticos.**
El [registro de implementación](PHASE_A_WAL_RECOVERY.md) fija el alcance admitido
y la cualificación pendiente en la interfaz con una decisión real del dueño.
No cierra A-69/A-71 ni la fase A. La fase B permanece posterior. Los journals de
rollback, incluidos los vacíos, siguen bloqueados en este primer incremento;
su recuperación continúa dentro del alcance pendiente de A.

## Referencia y evidencia

OC-18 estudia estáticamente cinco módulos de OpenClaw fijados al commit
`1e3ff306abaff29f03e7668d53b01c07f10294ac`: `sqlite-snapshot-source.ts`,
`sqlite-readonly-location.ts`, `sqlite-readonly-location-cleanup.ts`,
`sqlite-backup.ts` y `sqlite-private-directory.ts`, todos bajo `src/infra/`.
La procedencia, hashes y Evidence IDs corresponden al registro OC-18, no a este
plan. No se ejecutó la referencia ni se incorpora su implementación al producto.

Observaciones de los cuerpos seleccionados:

- La adquisición estricta prepara una familia privada antes de abrir un lector;
  la alternativa nativa fija una transacción y obtiene un backup.
- El WAL participa en la coherencia; el índice SHM se reconstruye en privado.
- Una ubicación preparada puede conservar sidecars: no equivale por sí sola a
  una imagen DELETE independiente ni a integridad SQL validada.
- Lectores, worker y retiro conservan propietario. Rechazo o cancelación no
  demuestran cierre nativo ni eliminación de bytes privados.
- El helper de prefijos WAL, los workers, tokens, retiro y helpers de filesystem
  importados no quedaron examinados en esta partición. No están acreditados su
  tratamiento de commits/colas, límites ni comportamiento ante crash.

Las reglas siguientes son **diseño propio de Jarvis**, no garantías heredadas
por leer esos archivos.

## Entrada y revisión

1. Admitir solo los tres roles fijos propios: estado, memoria y tareas. Conservar
   la lease física y el corte del host existentes; ningún store se inicializa
   para fabricar disponibilidad. El corte exige escritores cooperantes y no es
   una sandbox contra otro proceso del mismo usuario.
2. En el primer incremento, admitir main con cabecera WAL coherente y WAL
   presente no vacío, sin journal; SHM es opcional. Main ausente, sidecars
   huérfanos, cabecera mixta, enlaces, archivos inseguros y combinaciones no
   cualificadas se rechazan. Una cabecera WAL sin WAL queda pendiente de un
   escenario propio; nunca se normaliza a DELETE por cambiar dos bytes.
3. Fijar directorio y familia completa por descriptores/identidad privada:
   dispositivo, inode, usuario, modo, enlaces, tamaño, mtime y ctime. Incluir
   ausencias y la identidad de SHM aunque sus bytes no se reutilicen. La revisión
   rápida inspecciona metadata y cabeceras acotadas, sin cargar payload ni abrir
   SQLite original en el event loop.
4. Identificar explícitamente `cold_wal`/`cold_wal_snapshot` en los contratos que
   lo acepten. Un cambio de fuente, generación o destino invalida la revisión;
   no se reintenta silenciosamente sobre otra familia dentro de esa operación.

## Captura y validación

- El worker retiene el mismo corte durante toda la adquisición, transformación y
  limpieza. Revalida familia y barrera antes y después de cada lectura acotada.
- Copiar main y WAL a un directorio privado propio fuera del perfil fuente.
  Copiar mediante FDs fijados; comprobar contenido/identidad de la familia
  completa antes de aceptar la adquisición. Un stat estable aislado no demuestra
  que dos copias tomadas durante una escritura sean coherentes.
- Abrir SQLite únicamente sobre esa familia privada. Reconstruir SHM allí y
  obtener un online backup en RAM; validar después esquema propio, aplicación,
  versión, integridad, relaciones, hashes y límites con el inspector existente.
- La normalización de cabecera solo puede afectar al resultado cualificado del
  backup nativo, conforme al contrato actual; no parchear main/WAL originales.
- Mantener cerrada la carga de extensiones y usar `trusted_schema=OFF`. No
  ejecutar SQL procedente del archivo como instrucciones de la aplicación.
- La política implementada valida todos los frames, salts y checksums. Admite
  una cola completa válida sin commit después de un commit y recupera únicamente
  el último estado confirmado. Rechaza truncación, corrupción, generaciones
  mezcladas o ausencia de commits, sin omitir una cola ambigua para declarar éxito.
  SQLite interpreta la familia privada; la revisión muestra explícitamente que
  sólo incluye transacciones confirmadas. No acredita autenticidad del contenido.

## Presupuestos, cancelación y custodia

- Preflight separado para main, WAL, SHM, suma de familias, staging, imagen
  lógica resultante y máximo trabajo. Incluir expansión por número de páginas;
  no contar solo el tamaño físico de main. Conservar los límites portátiles de
  filas, celdas y VM del inspector y del perfil agregado.
- Comprobar cancelación, deadline y barrera antes de asignar buffers, crear
  staging, leer bloques y avanzar backup/validación. Un deadline de Python no
  convierte una llamada de filesystem o SQLite bloqueada en interrumpible.
- Registrar el propietario antes del primer archivo privado. Retirar solo
  entradas creadas por esta operación y con identidad vigente; no usar un
  borrado recursivo de una ruta cuya propiedad no se pueda acreditar.
- Cerrar consumidores y conexión nativa antes de eliminar sus bytes. La
  limpieza exitosa exige cierre confirmado y staging eliminado; una excepción
  del resultado no descarga esa obligación.
- Si queda un handle, worker o staging incierto, retener el mismo propietario y
  corte, publicar `cleanup_pending` y bloquear operaciones incompatibles.
  Una consulta lectora no debe resolverlo ni reejecutar la captura. Un cierre
  explícito puede intentar asentar solo la limpieza de ese mismo propietario.
- Separar ausencia de publicación, resultado desconocido y limpieza pendiente.
  No clasificar automáticamente toda excepción como ausencia de efectos.

## Integración real que requiere cambios coordinados

| Punto | Requisito |
| --- | --- |
| `cold_sqlite.py` | Diferenciar elegibilidad DELETE/WAL, pinning completo y captura con propietario explícito. |
| `host_profile.py` | Fingerprint y presupuestos de familia, `bind`, `doctor_images` y corte retenido. Mantener la revisión ligera. |
| `maintenance.py` | Revisar el catch de `frozen.bind`: hoy presupone lectura RAM sin staging. Preservar recibo/owner/limpieza incierta tanto en backup como en doctor. |
| `recovery.py` / `recovery_sqlite.py` | Consumir únicamente imagen canónica validada; conservar límites y separación entre captura y publicación. |
| `recovery_fs.py` | Custodia de staging privado por identidad, incluidos sidecars creados por SQLite y fallos de cierre/eliminación. |
| Memoria y DTO Swift | No habilitar la mutación de memoria por admitir backup/doctor WAL. `memory_host_cut.py`, `memory_host_read.py` y `RestoredMemoryModels.swift` conservan su contrato DELETE hasta una ampliación cualificada propia. |
| Documentación / matriz | Registrar alcance lector exacto y evidencia; mantener journals y demás recorridos como pendientes de A. |

No basta cambiar `ColdFamily.eligible`: estas conexiones y sus errores son parte
del incremento. La app instalada y los perfiles anteriores no se utilizan para
calificarlo.

## Matriz mínima de aceptación

Todos los casos de desarrollo usan perfiles y autorizaciones sintéticos. Los
fixtures de crash se producen en procesos propios y terminan antes de adquirir
la lease del perfil; no se hace pasar una familia viva por fría.

| Grupo | Caso y resultado exigido |
| --- | --- |
| Datos recuperables | Por cada rol, marcador confirmado existente solo en WAL; imagen final lo conserva y pasa inspector. Varias transacciones y páginas repetidas respetan el último estado confirmado. |
| Controles de lectura | Backup y doctor obtienen la misma imagen lógica sin inicializar stores ni conectar proveedor. Instrumentación impide cualquier `sqlite.connect` al original. |
| Original intacto | Bytes, identidades, permisos, tamaños, mtime/ctime y presencia de los cuatro miembros coinciden antes/después, en éxito, rechazo y cancelación. |
| SHM | Presente o ausente; se reconstruye solo en staging. Cambio de su identidad durante la operación invalida la revisión propia. |
| No admitidos | Journal vacío/no vacío, super-journal, main ausente, WAL huérfano, cabecera mixta, enlaces y permisos incorrectos: fallo explícito sin publicación. |
| Semántica WAL | Commit múltiple, cola no confirmada, truncación, checksum/salts/reset y datos corruptos: resultado conforme a política documentada; nunca éxito silencioso por omitir datos. |
| Carreras | Aparición, retirada, reemplazo y escritura de cada miembro entre revisión/lectura/validación; invalidación sin retry sobre otro snapshot. |
| Límites | Main pequeño con WAL grande o páginas lógicas enormes, agregado de roles, staging y salida; rechazo antes de superar presupuesto. |
| Cancelación | Antes de staging y durante copia/backup/validación; recibo preciso, originales intactos y cierre confirmado antes de liberar admisión. |
| Limpieza incierta | Fallos de close, unlink y fsync/identidad alterada retienen propietario/corte; estado consultable, sin replay ni borrado ajeno. |
| Cierre y concurrencia | Cancelaciones repetidas, cierre de app y nuevas lecturas no liberan custodia prematuramente. Solo la limpieza explícita del mismo owner puede asentarlo. |
| Integración | Recorrido Swift→IPC→backup/verificación/restauración y doctor sobre fixture WAL; después recorrido nativo limitado con aprobación real del dueño antes de afirmar disponibilidad cualificada. |

## Fuentes primarias para fijar el comportamiento

- [WAL y lectura de bases de solo lectura](https://www.sqlite.org/wal.html): WAL
  integra el estado persistente; abrir de solo lectura puede necesitar sidecars.
- [Formato WAL, commits, checksums y wal-index](https://www.sqlite.org/fileformat.html#walformat):
  SHM es transitorio; el formato define frames y límites de transacción.
- [Parámetros URI de SQLite](https://www.sqlite.org/uri.html#uriimmutable):
  `immutable=1` omite locks y detección de cambios; no acredita coherencia de una
  adquisición ni se aplica al original como atajo.
- [API online backup](https://www.sqlite.org/c3ref/backup_finish.html): comprobar
  fin y liberación; un cambio de la fuente puede reiniciar el backup.

El registro de implementación conserva las pruebas, identidades y límites. Este
primer incremento no acredita todas las familias WAL ni journals de rollback,
ni cierra los recorridos reales pendientes o la fase A.
