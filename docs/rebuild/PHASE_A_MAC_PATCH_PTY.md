# Parches, descargas y terminal propia de macOS

Este contrato propio amplía herramientas del workspace y procesos, dentro del
bloque de herramientas del Mac y navegador. No importa implementación, recursos
ni runtime de OpenClaw. No toca Terminal, credenciales o perfiles personales.

## Investigación por partes

La partición [OC-22A](REA_PART22A_SUMMARY.json) contiene cuatro fuentes TS y el
manifiesto, extraídos mediante `git show` de la revisión de referencia
`1e3ff306abaff29f03e7668d53b01c07f10294ac` a una carpeta privada nueva con archivos
de sólo lectura. Se conserva SHA-256 y blob Git de cada fuente. No se ejecutó
el clon ni se cambió su árbol. No se relajó la política de integridad de REA.

REA produjo el padre
`ev_e6487a9f6786e546c9ba11309b8d8f72bc80eafb6eff7e15a2207da4c4dc654e`
y la vista resumen
`ev_eb99fd1cfeabd10b2ffde9f3fc7267ef9728879356aacdbb1ea9e20bcac6ba38`.
La selección se parseó, pero la cobertura de aplicación sigue parcial y la
semántica desconocida, con **720 incógnitas**. Las dependencias externas a la
selección siguen sin revisar. La presencia de interfaces write/resize/kill y de
marcadores de parches es observación estática, no ejecución ni paridad completa.

El bundle privado de evidencia tiene SHA-256
`5200928e34dcb4e94b64b3cc3ef28dd831db0601735cedaa7597a56c65886e36`.
Las decisiones de diseño y su verificación siguientes son independientes.

## Diff unificado de texto

`workspace_patch` exige `{patch, expected_sha256}`. El texto usa cabeceras
`---`/`+++` y hunks `@@ -inicio,cantidad +inicio,cantidad @@` con coordenadas,
conteos y contexto exactos. Un prefijo `a/` o `b/` de cabecera se retira; el mapa
fija la ruta relativa resultante. `/dev/null` declara alta o baja. Cabeceras con
rutas diferentes declaran movimiento y admiten cambiar texto en el mismo paso.
Un movimiento sin hunks conserva bytes; alta/baja vacías son explícitas.

Cada ruta debe estar en `expected_sha256`: SHA-256 actual para un origen y `null`
para un destino nuevo. No hay selección implícita, búsqueda aproximada de hunks,
creación automática de directorios ni ejecución de shell, Git o una utilidad de
patch. Se limita a 16 operaciones, 96.000 bytes de patch y 32.000 bytes por archivo.
Las rutas agregadas tienen un presupuesto de 8 KiB, sin caracteres de control,
para que la revisión y los recibos permanezcan acotados.
Las rutas duplicadas/interdependientes se rechazan: no hay ciclos de movimiento.
El marcador estándar de ausencia de salto final conserva exactamente esos bytes.

Se aplican los mismos límites de rutas privadas, symlinks, hardlinks, propietario,
workspace físico y parent descriptors de las herramientas existentes. Se prepara
y sincroniza cada archivo temporal; después se revalidan **todos** los hashes y
fingerprints antes del primer efecto. La autoridad única concede la operación
exacta; cada publicación posterior revalida la misma lease ya comprometida.

La publicación es atómica **por archivo**, no una transacción del conjunto. Los
destinos nuevos se publican sin sobrescritura; los movimientos borran el origen
sólo después de verificar el nuevo archivo. Un fallo/revocación tras un efecto
queda `unknown` con datos de los archivos completados y la fase del archivo incierto, sin repetir el patch ni
restaurar automáticamente sobre cambios ajenos. Si la limpieza es incierta, el
resultado también lo conserva. No se atribuye aislamiento contra malware con el
mismo usuario. Parches binarios y cambios declarativos de modos de Git no forman
parte de este contrato UTF-8; editar conserva permisos ordinarios del origen.

## Publicación de descargas revisadas

`WorkspaceTools.publish_download` es el helper interno de `browser_download`.
No crea otra autorización: exige su lease original ya comprometida, argumentos
exactos con `path` coincidente y el guard fresco del host. Revalida la autoridad
antes y después del guard, y justo antes de publicar. Acepta como máximo 64 KiB,
crea únicamente un destino nuevo modo 0600 y no usa el nombre de una cabecera HTTP.
La relectura de los bytes, inode y hash verifica la copia. Se conservan las
restricciones de rutas/ancestros y la incertidumbre si falla después de publicar.

## PTY propia y aislada

`process_run` añade `pty: true` y `rows`/`cols` opcionales de 1 a 500, por defecto
24 × 80. El backend productivo de esta ampliación es macOS con Seatbelt. Si no
está disponible, falla cerrado; no cambia a un proceso sin sandbox ni a Terminal.
El modo de pipes existente conserva su contrato en macOS y Linux/bubblewrap.

El host crea un par `os.openpty`; el hijo propio obtiene su terminal de control
mediante un bootstrap constante `-I -S` y `TIOCSCTTY`, y reemplaza ese proceso por
el comando sandbox. El argv permanece separado del código constante. No hay
`preexec_fn`, importación de configuración Python ni un shell sin confinamiento.
La política permite sólo esa PTY concreta y el alias de su terminal de control;
red, fork y rutas privadas siguen denegados. El bootstrap conserva los descriptores
de custodia del perfil a través de `exec` hasta que termina el hijo cooperativo.

La salida y stderr están fusionados como corresponde a una terminal. El reader
no bloqueante conserva cursor, digest, límite de salida y timeout del supervisor;
los descriptores se cierran antes de señalar asentamiento. `process_send` requiere
su propia aprobación para bytes exactos. EOF lógico sólo se ofrece en modo
canónico; un terminal raw lo rechaza antes de escribir. Una entrega parcial,
revocación o cancelación detiene la carga propia y conserva incertidumbre.

`process_resize` requiere aprobación para proceso/filas/columnas y relee el tamaño
del kernel. `process_signal` permite sólo SIGINT o SIGTERM sobre el proceso propio
de esa conversación. Aceptar una señal no confirma su salida ni revierte efectos.
`process_stop` y el cierre del scope conservan el mismo dueño hasta recogerlo.

Este terminal sirve programas admitidos —incluido un REPL Python real— bajo el
mismo confinamiento de un solo proceso. No se anuncia como shell arbitrario con
pipelines o árboles de procesos. Permitir fork sin una nueva frontera de custodia
ampliaría los privilegios y haría falsa la recogida por PID único; se conserva la
prohibición. El ámbito más amplio de procesos distribuidos no queda certificado.

Las APIs utilizadas se contrastaron con las fuentes primarias de Python:
[`os.openpty`](https://docs.python.org/3.11/library/os.html#os.openpty),
[`termios` y tamaño de terminal](https://docs.python.org/3.11/library/termios.html),
[descriptores con asyncio](https://docs.python.org/3.11/library/asyncio-eventloop.html#asyncio.loop.add_reader)
y [subprocesos y descriptores heredados](https://docs.python.org/3.11/library/subprocess.html).

## Verificación del incremento

El focal final pasó **151 pruebas Python**, en
`/private/tmp/jarvis-patch-pty-qualified-final.log`: workspace, patch, procesos, PTY y
custodia. Incluye archivos reales, autorización central, alta/edición/baja/movimiento,
hunks múltiples, EOF exacto, preflight completo, límites, denegación, revisiones
obsoletas, rutas privadas, enlaces y efecto parcial sin replay. El helper de
descarga se prueba con su lease real, 64 KiB, no sobrescritura, revocación y cambio
de raíz. Las decisiones son sintéticas sobre datos privados nuevos de prueba.

La PTY se cualificó con Seatbelt real: isatty y grupo de terminal, tamaño observado,
REPL que calculó 7319, stdin y EOF, resize, SIGINT/SIGTERM, revocación tras un byte,
cancelación, límite de salida, fallo de arranque y lease flock retenida por el hijo.
También se probaron denegación de conexión a un listener propio, lectura/escritura
fuera del workspace y fork. Ninguna prueba controla una ventana de Terminal.

El primer focal de 16 casos tuvo 15 aprobados y uno fallido: su oráculo confundía
crear un socket con conectar a la red. Se corrigió para intentar una conexión a un
listener TCP sintético propio y comprobar la denegación real. El log inicial
`/private/tmp/jarvis-pty-first.log` conserva ese fallo; no fue un cambio de la
política de sandbox. La selección ampliada posterior pasó completa.

La cualificación GUI del bloque global, sus identidades, suites completas,
paquetes y CI se registran por separado por el coordinador. Compilar o estas
pruebas de contratos no cierran por sí solas toda la familia A-29/A-30 ni la fase A.
