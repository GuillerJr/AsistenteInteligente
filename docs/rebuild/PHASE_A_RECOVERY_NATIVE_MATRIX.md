# Matriz nativa finita de recuperación — 26-10-2026

Este registro amplía la [continuación de recuperación](PHASE_A_RECOVERY_COMPLETION.md)
con una matriz reproducible de **16 casos**, tres roles SQLite y ocho negativos.
Utiliza código y perfiles sintéticos propios. No ejecuta OpenClaw ni consulta
cuentas, historial o perfiles personales. La evidencia estructurada, identidades,
SHA y revisiones observadas están en [el registro JSON](PHASE_A_RECOVERY_NATIVE_MATRIX.json).
No declara cerradas A-71 ni la fase A.

## Cobertura finita

Los siete primeros perfiles contienen simultáneamente los roles `state`,
`memory` y `schedules`. Los nueve restantes dañan únicamente el WAL del rol
seleccionado; los otros dos roles quedan en DELETE independiente.

| Casos | Variantes y resultado esperado |
| --- | --- |
| `wal_absent_all` | WAL ausente; imagen privada del estado durable presente. |
| `wal_empty_all` | WAL vacío; imagen privada del estado durable presente. |
| `wal_header_all` | WAL con cabecera válida de 32 bytes; sin inventar frames ni transacciones. |
| `journal_hot_delete_all` | Journal DELETE caliente real, con múltiples segmentos; recuperar el estado anterior confirmado. |
| `journal_hot_persist_all` | Journal PERSIST caliente real; mismo contrato de recuperación privada. |
| `journal_inactive_persist_all` | PERSIST confirmado: cabecera invalidada; conservar el estado main y no aplicar restos. |
| `journal_inactive_truncate_all` | TRUNCATE confirmado: journal vacío; conservar main. |
| `prefix_truncated_{state,memory,schedules}` | Tercer commit truncado: prefijo hasta el segundo commit verificado; descartar 4119 bytes y un slot. |
| `prefix_checksum_{state,memory,schedules}` | Frame añadido con checksum inválido: conservar tres commits verificados; descartar 4120 bytes y un slot. |
| `prefix_old_generation_{state,memory,schedules}` | Frame añadido con salts de otra generación: mismo límite confirmado y descarte de 4120 bytes. |

Los fixtures de journal tienen 65 filas principales por rol. Los fixtures WAL
usan tres commits conocidos. Esa construcción proporciona un oracle; no permite
inferir que un WAL ausente en otra fuente nunca contuvo más transacciones.

El respaldo estricto rechaza los WAL dañados de los nueve casos de prefijo. Su
reparación requiere una revisión distinta, declara **pérdida posible** y mantiene
el número de transacciones confirmadas perdidas como **desconocido**. Ni la
revisión ni la publicación modifican el original.

## Runner y oracles

El [runner propio](../../script/recovery_qualification_matrix.py) ofrece:

```bash
.venv/bin/python script/recovery_qualification_matrix.py list
.venv/bin/python script/recovery_qualification_matrix.py prepare \
  --root /private/tmp/jarvis-q-nueva
.venv/bin/python script/recovery_qualification_matrix.py verify \
  --fixture /private/tmp/jr-q26-c/prefix_truncated_memory/fixture.json
```

`prepare` exige una raíz nueva y privada bajo el temporal admitido; no sobrescribe
una carpeta existente. Fija los casos, valida la longitud del socket Unix
(menos de 104 bytes) y rechaza rutas/casos ajenos. La tanda utilizada está en
`/private/tmp/jr-q26-c/matrix.json`, SHA-256
`0182f42ee3e85fa56e4cb179435045ff1717da2a640644deba4a1109624246f0`.
Su socket más largo ocupa 74 bytes.

Cada `fixture.json` conserva identidad del perfil, presencia y ausencia de los
cuatro miembros fijos de cada familia, metadata y SHA-256. Las lecturas usan FD
`O_NOFOLLOW` y directorios fijados. La metadata incluye dispositivo, inode, UID,
permisos, enlaces, tamaño, mtime y ctime. La preparación calcula filas completas,
IDs, ámbitos y FTS mediante SQLite nativo sobre **copias privadas**; nunca abre el
original con SQLite durante la cualificación.

`verify` comprueba el origen y, cuando se proporciona un destino fijo y su estado,
el inventario del artefacto, tamaños, hashes, identidad del destino y manifiesto
ligado al recibo. Deserializa únicamente bytes del artefacto en SQLite RAM con
`trusted_schema=OFF` y `query_only=ON`. Para un perfil restaurado comprueba además
el marcador y configuración desarmada. Sólo espera la transformación documentada
de tareas: `enabled=0` y `next_at=NULL`; las demás celdas del corpus se conservan.

El subcomando `serve` pertenece exclusivamente a fixtures IPC. Sus controles de
fallo, reloj, temporizador y terminación no se instalan en el producto. El
verificador devuelve `owner_decision_verified_by_this_script=false`: no autentica
al humano ni reproduce su clic.

## Pruebas automatizadas

- **20 Python aprobadas**: los 16 efectos completos contra la proyección
  independiente, límites de rutas/origen, restauración representativa desarmada
  y guardas de la intervención de transporte del fixture.
- **7 métodos XCTest con IPC real aprobados**: cuatro recorren los 16 positivos;
  tres recorren ocho escenarios negativos. Se usan clientes y DTO Swift,
  transporte autenticado, servicio Python y archivos reales propios.
- `RecoveryBootstrap.inspect` se ejecuta antes y después del recorrido. Sólo
  solicita `health` y `service.local_status`; los stores de los tres SQLite siguen
  cerrados, las familias originales conservan metadata/SHA y el proveedor recibe
  cero llamadas.
- Ruff y comprobación del diff propio aprobados. Estos conjuntos se solapan con
  la suite integrada; 16 escenarios no equivalen a 16 métodos XCTest adicionales.

| Negativo automatizado | Comprobación |
| --- | --- |
| N-01: WAL sin commit verificable | Rechazo de reparación; no aparece una aprobación ni un destino. |
| N-02: familia WAL/journal mixta | Rechazo de revisión antes de autorizar. |
| N-03: checksum de journal inválido | Fallo con recibo `no_published_effect`, sin alterar el origen. |
| N-04: denegación | `denied`, sin destino y sin repetir al reenviar la revisión. |
| N-05: cancelación previa al efecto | `cancelled`, sin destino ni nueva aprobación al reenviar. |
| N-06: deadline vencida | Reloj de fixture adelantado; `recovery_deadline_exceeded`, sin publicación. |
| N-07: TTL de autorización | Temporizador real de 0,08 s, presupuesto sintético; caduca sin decisión ni publicación. |
| N-08: interrupción del worker | Salida real del hijo mientras el worker espera antes de publicar; reinicio acotado, registro `interrupted_unknown` y ausencia de replay. |

Las decisiones de estas pruebas son **sintéticas**. La interrupción N-08 conserva
el staging. El arranque productivo primero rechaza el socket residual. Una
intervención explícita del fixture, tras confirmar la salida del hijo y obtener
el lease exclusivo, valida dispositivo/inode/UID/permisos y retira **sólo** ese
`service.sock`. El transporte nuevo se verifica. Esto no implementa ni acredita
una autorrecuperación del producto.

## Observaciones GUI y decisiones del dueño

La app se inicia con **`--recovery-only`**, una bandera local de la interfaz.
Evita enumerar sesiones y tareas durante el bootstrap: la apertura normal de sus
stores puede consumir WAL/journals antes de llegar a Mantenimiento. No concede
autoridad RPC ni constituye una sandbox.

Con el binario GUI01 SHA-256
`084540d84b961e4776809e4fb92f3162413da4f51af00a7451f7b3563fd86aba`,
se observaron las 16 revisiones por accesibilidad/CUA. El aviso de conservación
aparece en todas y el de pérdida en las nueve reparaciones. Los 16 arranques
terminaron con código 0; los oracles de antes y después conservaron las fuentes.
Los digests individuales están incorporados al JSON. Los tiempos del registro
incluyen interacción humana y no son métricas de rendimiento.

Estas observaciones son **revisiones GUI**, no 16 publicaciones humanas. Los
efectos de toda la matriz se acreditan separadamente mediante IPC automatizado.
Las decisiones reales seleccionadas corresponden al caso `prefix_truncated_memory`:

| Operación | Resultado registrado |
| --- | --- |
| `9097324a6cb4490993add26c0dd3d10f` — reparación | `complete`, recibo durable y manifiesto `d3d34ec5…ddeb57`; recupera el segundo commit de memoria con descarte explícito de 4119 bytes. |
| `4e929179631c4ccd9c56f2d558dd6aa8` — restauración | `staged_disarmed`, recibo durable y mismo manifiesto de origen; destino nuevo sin activar ni abrir. |
| `cf370181e36243358987427f8e66042c` — denegación | `denied`, registro durable, `receipt=null` y sin worker pendiente. |
| `6750835ab8d34f2fa59e8035bbaabd7e` — cancelación | `cancelled`, registro durable, `receipt=null` y sin worker pendiente. |
| `31b1c5cc19f2421482cc28b48c141811` — caducidad de restauración | `failed`, `maintenance_review_expired`, registro durable y sin recibo de publicación. |

El runner volvió a verificar, por lecturas independientes, el respaldo reparado,
el perfil restaurado y las tres familias originales: integridad, proyección SQL,
configuración desarmada y hashes coincidentes. Su resultado no identifica al
humano: la atribución de las decisiones se apoya en la interacción registrada
con el dueño y el estado del host; no se instrumentó físicamente su clic.

El [registro JSON](PHASE_A_RECOVERY_NATIVE_MATRIX.json) también conserva la caída
GUI03 de la operación `a6cf91e9f0344c248388d10f465719ff`, mientras esperaba al dueño,
sin autorización entregada. El destino permaneció ausente y el origen intacto.
Después de la intervención explícita de transporte del fixture apareció
`interrupted_unknown`, sin replay; se observó el aviso nativo correspondiente.
Esta caída en espera y la caída automatizada durante un worker son casos distintos.

## Límites del registro

Este documento no acredita la reapertura GUI final ni la solución de tickets
desconectados de la revisión 05. Tampoco establece la causa o corrección universal
de incidencias anteriores de ventanas o tarjetas. Esos recorridos requieren su
propia evidencia de cierre.

La matriz es finita: no promete recuperación forense, B-trees arbitrariamente
dañados, super-journals, archivos borrados ni toda combinación SQLite posible.
No se probaron perfiles personales. La autenticidad de distribución, TCC,
instalación autocontenida y cierre global de la fase A conservan sus límites.
