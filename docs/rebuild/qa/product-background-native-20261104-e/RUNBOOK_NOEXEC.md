# QA nativa de desarrollo E: pregunta en A, aislamiento en B

Borrador NOEXEC. La raíz revisa los pines y confirma la CI completa antes de
montar o lanzar. Esta app exige Python, código, dependencias y Codex externos
seleccionados explícitamente; no es una distribución autocontenida. Los tres
intentos de descarga grande anteriores terminaron sin app y sus imágenes,
fuentes, recibos y cierres normales se conservan.


## Cambio mínimo frente a D

D preparó las copias correctamente y lanzó la app una vez, pero su supervisor
terminó con metadata indeterminada antes de admitirla. No hubo login, modelo ni
herramientas. La raíz después contrastó la misma identidad, cerró esa misma app
por CUA y comprobó tres identidades conocidas ausentes, lease libre posterior y
un desmontaje normal. El `closed.json` original sigue incierto; el cierre de raíz
no lo reescribe como salida normal del supervisor. El registro no contenía el
PID/errno de la muestra fallida, así que no establece si fue estado de arranque
o un helper terminado aún pendiente de reap.

E sólo cambia `common.py`. Para una lectura BSD/path no disponible realiza
hasta10 muestras/2s; los pines de identidad observada no se relajan y nunca
reintenta efectos. El Popen exacto creado por capture se registra antes del
callback; poll/reap se hace antes de los checkpoints y de nuevo dentro de la
lectura de ese hijo si falla la metadata. Así no depende de acertar una ventana
entre el poll anterior y la terminación del helper. La auditoría acotada conserva
PID/retorno/errno y contadores; no hay argv ni entorno. No se cambian los límites
300/810+90, cuotas, oráculos, fuente ni artefacto. Nunca consultar handles CUA
viejos; adquirir sólo la nueva app ya confirmada, preferiblemente por su ruta.

## Identidad y límites

- HEAD de producto: `2c8259de8112aa5f1487814a5c5c35b3931e5c43`.
- CI: `41842491175`, intento1, Ubuntu126617416540 / macOS126617417127.
- Sólo artefacto `jarvis-native-qa-<HEAD completo>`, ID13329917886.
- ZIP7698129bytes, SHA3a7dd21a3a92210aa602c72039a0378460ba98a0a9c6168e12762efc266a6f16.
- Mismo APFS2GiB: ZIP/app exacta CI, código/deps/Codex copiados, estado,
  proveedor nuevo, HOME y TMP. Preparación300s; app810s+90s de cierre.
- Data≥25GiB; caída residual≤1GiB; proveedor128MiB muestreado por metadatos con
  caché reutilizada hasta1s y muestra fresca antes de efectos; máximo128PIDs
  conocidos. No se afirma una sandbox frente a otro proceso del mismo UID.

El ZIP nativo exportado conserva cuatro ficheros exactos: main, helper, Info y
CodeResources. No se cambia su ID ni firma localmente. Se valida su provenance,
firma, bytes completos y secciones __TEXT antes de preparar los argumentos.
No hay `JarvisRuntime`, manifiesto de runtime ni config de cualificación.

## Entradas locales

Admisión fija dos programas Python distintos: el launcher `.venv.nosync/bin/python`
(SHA50de159a94723fa71090030ac642b101e27f8d29488ec4bdae91edfa1e86dbbd) y su
proceso real `Resources/Python.app/Contents/MacOS/Python`
(SHAe17826d56c53bce1083f70b4cdb68ea29105fdc84c1c345746523f2af0b8287d).
La detección de `active` exige la ruta real de proceso, UID, padre y nacimiento.

Sólo `src` versionado se copia, contrastando cada blob con Git HEAD. Los seis
paquetes locales declarados se contrastan en METADATA sin importarlos. Falta
soundfile; su importación Ogg es diferida y este caso no ejercita medios.
No se leen/copían auth.json, direct_url.json, claves privadas, hooks .pth ni
cachés pyc. Codex oficial0.163.0 se copia desde el Mach-O instalado y su SHA
8813367285524ac798dd96d1b071aea30d58708c7226fd236cd275cf1cfdee31.
Todo destino es nuevo y físico en la cuota; no se instala ni compila nada.

El manifiesto `copies-manifest.json` tiene límite4MiB y liga hash, inode,
tamaño, permisos y tiempos de cada copia pública. No es un recibo ni contiene
credenciales. Las entradas completas se comprueban al entrar; antes del efecto
se revalidan sus identidades/metadatos sin volver a hashear400MiB.

## Preparación y lanzamiento

Usar el intérprete seleccionado con `-I -S -B` y bootstrap del directorio de
scripts. Los siguientes marcadores se sustituyen con la admisión/pin real:

```sh
/opt/homebrew/Cellar/python@3.11/3.11.15_1/Frameworks/Python.framework/Versions/3.11/bin/python3.11 -I -S -B -u -c 'import runpy,sys;from pathlib import Path;script=sys.argv[1];sys.argv=sys.argv[1:];sys.path.insert(0,str(Path(script).parent));runpy.run_path(script,run_name="__main__")' /private/tmp/jarvis-product-background-native-20261104-e/prepare.py --admit-preparation --admission-sha256 ee992646c046698205b8b8215d4368f1440c329a3fc6cb753ff23123ce671185
```

La preparación pide metadata oficial y exige todo el workflow aprobado, ambos
jobs exactos y el artefacto nativo correcto antes del volumen. El ZIP8MiB usa
GH API normal, sin enlaces privados ni reanudar archivos parciales.
Conservar el `prepared_sha256` devuelto y luego, por el mismo bootstrap:

```sh
<SELECTED_PYTHON> -I -S -B -u -c '<SCRIPT_BOOTSTRAP>' /private/tmp/jarvis-product-background-native-20261104-e/supervise.py --admit-launch --prepared-sha256 <PREPARED_SHA>
```

El helper recibe exactamente doce argumentos: seis pares `--python`, `--source`,
`--dependencies`, `--state`, `--codex`, `--provider-state`. Todos los datos/copias
van a `/private/tmp/jq-*`; el intérprete externo queda fijado. No usar catorce
argumentos ni añadir flags que el helper no admita.

No adquirir CUA hasta ver `active` y la app/servicio correctos. ID esperado:
`local.jarvis.qa.native.2c8259de8112`. Dirigirse a su ruta exacta fijada por el
supervisor. Si el acceso de ChatGPT aparece desconectado, el dueño completa el
inicio de sesión oficial desde esta app; no se leen ni recuperan credenciales.
El plazo no se amplía durante esa espera. No pedir permisos OS/TCC de avisos.

## Único recorrido

1. A vacío, Actividad y panel: activar avisos y silenciarlos, revisión exacta2,
   enabled=true/silenced=true. Capturar AX1/1.
2. Crear sólo B: vacío/pref0falsefalse. Volver A, AX1/1.
3. Probe inventory y before con recibos exclusivos: dos chats vacíos, A2/B0.
4. Enviar `admission.json:model_prompt` una vez. Debe crear sólo una pregunta
   canónica `background_gate`; no aprobar ninguna otra herramienta.
5. Con A pendiente, cambiar B y capturar AX0/0/vacío. Probe waiting comprueba A
   running, pregunta pendiente rev1, cero recibos y avisos, B vacío/pref0.
6. Después de waiting, observar de nuevo B y escribir atestación CUA real;
   responder una sola vez con answeronce. B permanece delante.
7. Tras completar A, observar B0/vacío. Probe after verifica mismo registro
   consumido rev3, sin respuesta disponible, un recibo conocido sin valores
   ni permisos, un run modelo completado con limpieza confirmada.
8. Volver A y observar AX1/1 y respuesta sintética. Ninguna inferencia adicional.

## Probes y atestación

Bootstrap explícito, HOME/TMPDIR de prepared y source/dependencies de la cuota:

```sh
env -i HOME=<QUOTA>/launcher-home TMPDIR=<QUOTA>/launcher-tmp PATH=/usr/bin:/bin LANG=C PYTHONUTF8=1 <SELECTED_PYTHON> -I -S -B -u -c 'import runpy,sys;from pathlib import Path;core,deps,script=sys.argv[1:4];sys.argv=[script,*sys.argv[4:]];sys.path[:0]=[core,deps,str(Path(script).parent)];runpy.run_path(script,run_name="__main__")' <QUOTA>/source <QUOTA>/dependencies /private/tmp/jarvis-product-background-native-20261104-e/probe.py inventory --prepared-sha256 <PREPARED_SHA> --receipt /private/tmp/jarvis-product-background-native-20261104-e/inventory-a.json
```

before/waiting/after añaden `--session-a A --session-b B`; waiting/after también
`--run-id RUN`. Waiting escribe sólo `ROOT/waiting.json`; after recibe su SHA
con `--waiting-sha256`. Los recibos se acotan a64KiB y cada probe a40s.

`ui-before-answer.json` sólo se escribe tras observar de verdad:

```json
{
  "schema": "jarvis.background-question-root-cua-attestation/1",
  "source": "root_cua_public_accessibility",
  "prepared_sha256": "<PREPARED_SHA>",
  "waiting_sha256": "<WAITING_SHA>",
  "selected_session_id": "<B>",
  "enabled_AX_value": 0,
  "a_pending_question": true,
  "app_pid": 123,
  "bundle_identifier": "local.jarvis.qa.native.2c8259de8112",
  "observed_at_unix": 0,
  "evidence_reference": "Root CUA own QA window: B selected/AX0 while A canonical pending"
}
```

PID/hora son marcadores. Deben corresponder al app actual, después de waiting y
con antigüedad≤60s al efecto. answeronce exige `--admit-one-answer`, pines de
waiting y atestación, `--receipt ROOT/answer.json`; deriva A/B/run del waiting,
no admite esos argumentos en la mutación. Escribe intención O_EXCL antes del
único RPC. Un ACK incierto nunca se repite aunque después siga pendiente.

## Cierre

Antes del810, por el bootstrap de scripts:

```sh
<SELECTED_PYTHON> -I -S -B -u -c '<SCRIPT_BOOTSTRAP>' /private/tmp/jarvis-product-background-native-20261104-e/supervise.py --request-GUI-quit --prepared-sha256 <PREPARED_SHA>
```

Después CUA estado actual y CmdQ en la app exacta. No consultar/readquirir ese
handle después. El supervisor confirma identidades conocidas ausentes, mismo
lease libre y desmontaje normal≤2intentos, sin fuerza ni borrado.
Cierre aprobado exige no señales, errores ni brecha de recursos, además de los
cuatro estados UI y probes completos. SIGTERM de la app confirmada sólo se usa
para límite/fallo del supervisor; no puede presentarse como salida GUI normal.
Un returned_candidate_unconfirmed es información, nunca autoridad de señal.

Conservar fuentes/admisión/checklist y recibos saneados en el repo. No exportar
perfil, proveedor, desktop.key ni auth.json. El caso anterior de seis etapas
sigue incompleto donde no observó B durante A; este recorrido independiente
aporta esa observación sin reescribir su resultado. La faseA global y B no se
cierran por esta prueba nativa de desarrollo.
