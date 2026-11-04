# Caso nativo independiente: B mientras A espera

Borrador independiente B congelado el 04-11-2026. Este archivo no autoriza una ejecución.
El dueño delegó las decisiones de los perfiles sintéticos. La raíz revisa y
opera la única ventana propia de QA; nunca abre el historial del dueño.

No se ejecutaron scripts, suites, builds, descargas, montajes ni apps al escribir
este borrador. AST y Ruff F/E9 son comprobaciones estáticas sin importar fuentes.

## Identidad y preparación

Exigir primero conclusión completa de CI `41123446861`, intento 2, SHA
`1b0a7ead49f2659ef536d47947a9ed7364d0e632`. El ZIP nuevo es el artefacto
`13323672851`, digest
`5ee799ebece9726526305e7a9090bc2b3a7d7621f1782e7fdd23cef8f42af19c`.
El artefacto anterior de octubre expiró; nunca se reconstruyen sus bytes.

Sólo después de la revisión de raíz y CI verde:

```sh
.venv/bin/python -B /private/tmp/jarvis-product-background-20261104-b/prepare.py --admit-preparation --admission-sha256 b60b24b5eff2ff674313653410d49fa11921e569f48f24dbba4fa666b4b6a354
```

`prepared.json` fija la raíz `/private/tmp/jq-*` nueva, el bundle y los hashes.
Conservar su SHA de salida. Preparación fallida queda preservada, sin lanzamiento
ni borrado de la imagen; no se borra una intención para reintentar.

```sh
.venv/bin/python -B /private/tmp/jarvis-product-background-20261104-b/supervise.py --admit-launch --prepared-sha256 <PREPARED_SHA>
```

Mantener la sesión del supervisor. Sólo al observar `active.json` se puede
adquirir la app `local.jarvis.qa.background.20261104b.1b0a7ead` mediante CUA.
El supervisor observa únicamente metadatos de PIDs conocidos y sus hijos.

## Cuenta nueva dentro de la cuota

Esta variante crea `provider` nuevo y privado dentro del mismo APFS2GiB. No usa
el proveedor externo anterior ni recorre sus directorios. Si aparece desconectado,
el dueño completa el acceso oficial de Codex con ChatGPT desde esta app; nunca
se leen ni copian auth.json ni se introducen credenciales por él. No iniciar el
único run hasta observar la conexión. La ventana de810s no se amplía si falta
el dueño: se cierra normalmente antes del plazo y se conserva ese resultado.

El subpresupuesto de128MiB del proveedor se mide por metadatos con caché por
proceso reutilizada hasta1s; siempre se renueva al entrar y antes de efectos
launch/answer/detach. Todos sus bytes siguen dentro del límite físico agregado
2GiB también entre muestras. Esta medición no se presenta como una sandbox
física adicional. El residual de Data no descuenta dos veces el proveedor,
pues ya forma parte del uso del mismo volumen.

El intento anterior agotó sus300s de preparación sin lanzar la app. Su imagen,
fuentes e intenciones se conservan; el desmontaje normal quedó comprobado en
`preparation-closeout.json` con SHA19d7d72cc470b8c5ab4ef9db0295b0f5c5a1565df3c4ef1a9e473d3926c0dbb0.
Este borrador cambia la ubicación/frecuencia de medición en otra raíz; no alarga
el intento anterior, usa su descarga parcial ni repite un lanzamiento incierto.

## Secuencia finita

1. A nuevo vacío: abrir Actividad y panel. Activar avisos y silenciarlos: revisión
   exacta 2, enabled=true, silenced=true; AX1/1. No pedir permiso de macOS.
2. Crear sólo B, vacío, pref0=false/false; volver A y capturar AX1/1.
3. Probe `inventory` revela sólo los IDs del perfil sintético. Probe `before`
   exige A/B vacíos y sus preferencias exactas. Usar recibos exclusivos nuevos.
4. Enviar una sola vez `admission.json:model_prompt`. Esperar la tarjeta
   `background_gate` pendiente. No aprobar ninguna otra herramienta.
5. Cambiar B, capturar AX0/0 y vacío. Probe `waiting` exige A running, pregunta
   canónica rev1 pendiente, cero recibos y cero avisos; conservar `waiting.json`.
6. Obtener una AX nueva de B después de `waiting.json`. Crear
   `ui-before-answer.json` con el contrato de atestación que valida `probe.py`:
   prepared/waiting SHA, PID/bundle exactos, B seleccionado AX0, A pendiente,
   tiempo de observación posterior al waiting y antigüedad máxima 60 s.
7. Probe `answeronce --admit-one-answer`: una sola respuesta sintética con intención
   exclusiva antes del RPC. No seleccionar A ni pulsar su tarjeta; B queda delante.
   ACK exacto de cuatro campos. Cualquier incertidumbre conserva el recibo y
   nunca permite repetir el efecto, aunque una lectura posterior vea pending.
8. Observar B AX0/0 después de completar A. Probe `after` con pin waiting exige
   pregunta consumida rev3 y sin respuesta disponible, un recibo conocido sin
   valores/autoridad, un run completado con limpieza confirmada y B vacío.
9. Volver A, capturar AX1/1 y la respuesta final sintética. Ninguna nueva inferencia.

Los probes requieren el Python **del bundle** `-B -s`, con `PYTHONPATH` limitado
a `JarvisRuntime/core` y `JarvisRuntime/site-packages`, HOME/TMPDIR nuevos de la
cuota, PATH fijo y `PYTHONNOUSERSITE=1/PYTHONDONTWRITEBYTECODE=1`.

Argumentos comunes: `--prepared-sha256 <pin> --receipt <ROOT/nombre-nuevo.json>`.
`before/waiting/after` añaden `--session-a <A> --session-b <B>`; waiting/after
requieren `--run-id <RUN>`. Waiting escribe exactamente `ROOT/waiting.json`.
After añade `--waiting-sha256 <pin>`.
Answeronce sólo acepta `--waiting-sha256 <pin> --ui-attestation-sha256 <pin>
--admit-one-answer --receipt ROOT/answer.json`; deriva A/B/run del waiting fijado.
No permite los argumentos session/run durante la mutación.

## Cierre antes de 810 segundos

```sh
.venv/bin/python -B /private/tmp/jarvis-product-background-20261104-b/supervise.py --request-GUI-quit --prepared-sha256 <PREPARED_SHA>
```

Después CUA en la app exacta: estado actual y CmdQ. No consultar ni readquirir
ese handle tras salir. Registrar el resultado CUA y esperar el supervisor.
Cierre feliz exige `closed_after_GUI_intent`, sin señales, cuatro estados UI,
probes positivos, identidades conocidas ausentes, mismo inode del lease libre y
cuota desmontada normalmente. Las imágenes/perfiles se conservan.

SIGTERM sólo está permitido para la app de nacimiento/UID/ruta confirmados ante
plazo, recursos o fallo del observador. Ese cierre no puede pasar la puerta normal.
Una identidad returned_candidate_unconfirmed no concede autoridad de señal.
El plazo total es 900 s; a los 810 s se reserva el cierre. Un cierre incierto no
borra, fuerza un desmontaje ni repite el lanzamiento.

La raíz debe conservar fuentes/admisión/checklist y recibos **saneados** en el repo
al finalizar: no exportar perfiles, claves, cookies ni auth.json. Este caso no
convierte el intento de seis etapas del 30-10 en aprobado ni cierra familias
amplias o la fase A por sí solo.

## Bootstrap aislado alternativo (preferido)

`-I -S -B` también satisface `no_user_site` y evita depender de PYTHONPATH.
Sustituir los marcadores con las rutas exactas fijadas en prepared.json:

```sh
env -i HOME=<QUOTA>/launcher-home TMPDIR=<QUOTA>/launcher-tmp PATH=/usr/bin:/bin LANG=C PYTHONUTF8=1 <QUOTA>/Jarvis.app/Contents/Resources/JarvisRuntime/python/bin/python3.11 -I -S -B -u -c 'import runpy,sys;from pathlib import Path;core,deps,script=sys.argv[1:4];sys.argv=[script,*sys.argv[4:]];sys.path[:0]=[core,deps,str(Path(script).parent)];runpy.run_path(script,run_name="__main__")' <QUOTA>/Jarvis.app/Contents/Resources/JarvisRuntime/core <QUOTA>/Jarvis.app/Contents/Resources/JarvisRuntime/site-packages /private/tmp/jarvis-product-background-20261104-b/probe.py inventory --prepared-sha256 <PREPARED_SHA> --receipt /private/tmp/jarvis-product-background-20261104-b/inventory-a.json
```

Contrato de `ui-before-answer.json`, escrito sólo tras la observación real:

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
  "bundle_identifier": "local.jarvis.qa.background.20261104b.1b0a7ead",
  "observed_at_unix": 0,
  "evidence_reference": "Root CUA own QA window: B selected, enabled0, A canonical pending"
}
```

PID y hora son marcadores; no usarlos como observación. La hora debe ser posterior
al waiting y menor de 60 segundos al responder. El hash debe ser de los bytes
exactos que escribió raíz y luego enviarse como `--ui-attestation-sha256`.
