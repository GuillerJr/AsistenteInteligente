# Núcleo local: identidad del cliente y recuperación del servicio

Este contrato es propio y se integra en la continuación de núcleo y sesiones.
El alcance es el servicio local de macOS, sus clientes nativos y los procesos
controlados por los adaptadores propios. No representa identidad Apple firmada,
acceso remoto, otro usuario del sistema ni distribución autenticada completa.

## Investigación por partes

La partición [OC-21A](REA_PART21A_CORE_SERVICE.json) fija manifiesto y tres fuentes
de la referencia OpenClaw `1e3ff306abaff29f03e7668d53b01c07f10294ac`.
La primera lectura sobre el árbol de consulta detectó que `appcast-x86_64.xml`
cambiaba durante la adquisición. Se rechazó esa identidad, sin seguir como
confiable ni modificar el clon. La partición posterior se obtuvo de los blobs
Git exactos, con nombres, hashes y tamaños registrados.

REA conservó el padre
`ev_ade6f59ec7ea1c09c1f5458558226b3aeabfab54376fd9808062360d4536db7b`
y la vista
`ev_c14b62ae79fcfc1a01bc296b420cbe07adf22615d9f93f65c89b1314622ac38f`.
La cobertura de aplicación es parcial y la semántica permanece desconocida;
243 incógnitas semánticas y dependencias no seleccionadas siguen explícitas.
El análisis fue estático: no se ejecutó OpenClaw ni se trasladó su implementación.

Las observaciones que informan el diseño son la separación entre identidad,
almacenamiento y ámbitos de cliente, y la necesidad de comprobar observación
física y dueño vivo antes de recuperar un recurso. Los contratos siguientes
son decisiones propias, con pruebas separadas de esos hallazgos estáticos.

## Cliente admitido y solicitudes firmadas

`JarvisService` exige una `ClientAuthority`; un bearer `transport.token` por sí
solo no permite leer estado ni invocar operaciones productivas. El servidor
genérico conserva su modo sin autoridad únicamente para probar ese contrato de
transporte por separado; la app no negocia una bajada automática a ese modo.

El escritorio genera una clave Ed25519 propia. La firma usa CryptoKit en Swift
y la verificación usa la dependencia fijada `cryptography` en Python; no se
implementan primitivas criptográficas propias. Al lanzar el servicio se pasa
únicamente el pin público elegido por ese proceso. La primera admisión no se
puede crear mediante un token obtenido del socket. Los lanzamientos posteriores
no readmiten un pin revocado ni cambian un registro existente por otro pin.

El registro público y la clave del escritorio están en `<perfil>.clients`,
fuera de los archivos portátiles del perfil. El registro fija la identidad
física dispositivo/inode del perfil. La clave privada es un archivo propio
de 32 bytes, modo 0600, un enlace y ancestros sin symlinks. Una copia restaurada
requiere identidad y admisión nuevas; no hereda estas credenciales.

Cada conexión solicita un nonce nuevo para un cliente ya admitido. Cada
solicitud firma dominio del protocolo, perfil físico, identidad efímera del
servidor, nonce, cliente, secuencia estricta y SHA-256 del cuerpo JSON exacto.
El cuerpo se transporta como bytes codificados, evitando diferencias de
canonicalización numérica entre Foundation y Python. No se aceptan la misma
secuencia, firmas de otro cuerpo, otra conexión ni otra instancia de servidor.

Los ámbitos son `owner` y `read`. El segundo sólo accede al catálogo explícito
de lecturas y no puede admitir clientes, aprobar herramientas ni mutar sesiones.
La admisión adicional requiere revisión exacta, revisión CAS del registro,
identidad del propietario y vencimiento de 120 segundos. Su revisión es de un
uso. La revocación persiste, se reconsulta en cada solicitud incluso dentro de
un socket ya conectado y no puede eliminar al último propietario activo.

Los controles nativos de clientes usan el mismo corte del host. Las firmas no
sustituyen las aprobaciones de herramientas: éstas conservan la autoridad única
por operación, sesión, run y argumentos. El cliente que inició un run se conserva
en su contexto; revocarlo retira sus aprobaciones pendientes y cancela sus runs.
También se revalida antes del proveedor y justo antes de entrar en el efecto.
Los hijos de coordinación mantienen esa identidad, además de sus propios límites.

Esto acredita posesión de una clave local admitida. No es una atestación del
binario macOS ni protección contra un programa malicioso con el mismo usuario
que pueda leer la clave o modificar el registro. Esa frontera no se oculta
llamando «firma» a un bearer ni atribuyendo a 0700 una sandbox del sistema.

## Caída, custodia de hijos y arranque

El servicio conserva su flock exclusivo de `profile.lock`. Los tres adaptadores
de procesos —Codex, MCP y procesos de sandbox— reciben una callback del host
que valida su lease y heredan el mismo open file description mediante
`pass_fds`. Si muere el servicio y un hijo controlado mantiene ese descriptor,
el nuevo `ProfileLease.acquire` falla antes de abrir SQLite, configuración o
otro recurso del perfil. No se borra un lock ni se decide por un PID reciclable.

El supervisor nativo fija el perfil físico después de la salud autenticada.
Para preparar recuperación exige la salida observada del hijo propio por el
kernel, contexto de lanzamiento idéntico, ausencia de un cierre aún incierto,
perfil físico idéntico y flock obtenible en un `profile.lock` regular propio,
modo 0600, un enlace y tamaño cero. Revalida inode y perfil tras obtenerlo.
Se conservan el recibo anormal y su advertencia; no se convierte en cierre
gracioso. El límite es dos recuperaciones por minuto.

La app puede supervisar ese recorrido acotado y volver a cargar únicamente
estado local. No vuelve a iniciar un run, una aprobación, Telegram o una tarea,
ni consulta el SDK por recuperar el servicio. El estado durable interrumpido
se conserva como `interrupted/unknown`; el chat temporal perdido sigue perdido.

El arranque siguiente retira exclusivamente un socket propio modo 0600 bajo
el lease actual, después de `ECONNREFUSED` y una segunda comprobación de inode
y ctime. Un listener vivo, un errno ambiguo, permisos inseguros, un symlink o
una sustitución conservan el recurso y bloquean la recuperación. La adquisición
del nuevo lease antes de abrir stores vuelve a ser la comprobación definitiva.

La custodia cubre hijos cooperativos controlados. Un programa que cierre o
desbloquee deliberadamente el descriptor, o un descendiente externo escapado,
no queda certificado por este mecanismo. No se afirma control universal de
árboles de procesos ni aislamiento frente a malware del mismo usuario.

## Arranque aislado y dependencias fijadas

Se conservan `-I -S -B -u` y el intérprete canónico elegido. `--source` y el
nuevo `--dependencies` son rutas explícitas pasadas como argumentos, sin
interpolación de código. El bootstrap añade sólo esas rutas a `sys.path`; no
ejecuta `site.main`, `.pth`, `sitecustomize`, `usercustomize` ni busca otro venv.
La dependencia criptográfica se cualifica con el paquete y su lock. Esta ruta
de desarrollo sigue necesitando componentes externos y no es una distribución
autocontenida o notarizada.

## Evidencia y pruebas

Las pruebas propias cubren dos identidades distintas, ámbitos, admisión y
revocación durables, CAS, vencimiento, cambio de dueño, cuerpo/secuencia/nonce/
servidor/perfil alterados y credenciales inseguras. La revocación de un cliente
con una escritura esperando aprobación termina cancelada y no crea el archivo.
Los mensajes excesivos rechazados antes de escribir no consumen la secuencia.

Las pruebas de kernel demuestran que cada uno de los tres adaptadores mantiene
el flock después de cerrar el descriptor del host, hasta que cierra el hijo.
Otro recorrido mata sólo un servicio sintético propio con SIGKILL: su SQLite
conserva el run interrumpido y no repite la llamada. Una variante deja un MCP
propio vivo tras esa caída; otro host falla con `profile_busy` sin cambiar los
bytes SQLite. Tras liberar ese hijo, el arranque local vuelve a funcionar.

El primer control Swift integrado conservaba una expectativa del checkpoint
anterior: exigir fallo de arranque y retirar el socket mediante un helper de
tests. El contrato nuevo ya recuperaba el socket muerto de forma segura; esa
expectativa falló y la intervención obsoleta produjo después un error al leer
su supuesto oráculo JSON. Se conserva el fallo en el log del control inicial.
La regresión histórica ahora arranca mediante el producto y exige el indicador
de recuperación, un socket nuevo, fuentes intactas, worker anterior conservado
como `interrupted_unknown`, staging intacto y rechazo de la revisión anterior.
Las negativas de listener vivo, lease ocupado y perfil cambiado permanecen.
El focal integrado de esa regresión, colas, delegación y supervisor pasó
25 pruebas en `/private/tmp/jarvis-core-crash-contract-native.log`.

El focal nativo registrado en
`/private/tmp/jarvis-core-client-native-qualified-final-2.log` pasó 31 pruebas,
incluyendo dos clientes Swift→Python y las barreras de recuperación. El focal
Python inicial y los recorridos posteriores tienen logs separados en
`/private/tmp/jarvis-core-service-first.log`,
`/private/tmp/jarvis-crash-orphan-qualified-2.log` y
`/private/tmp/jarvis-core-service-final-focused.log`. Los casos forman parte de
las suites integradas; no se suman como ejecuciones independientes del producto.

Los fixtures crean claves, cuentas de peer y decisiones sintéticas. El dueño
delegó las aprobaciones de esos perfiles al agente. No se leyó `auth.json`,
historial personal ni credenciales del dueño; no se usaron permisos TCC o
la app personal instalada. El cierre integrado registra aparte compilación,
recorridos GUI, paquetes y CI del commit final.
