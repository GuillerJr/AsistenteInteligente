# Caso G: aislamiento nativo, nuevo CI y proveedor acotado

Estado: TEMPLATE NOEXEC. No se han importado ni ejecutado estos scripts, ni se ha
montado, descargado, autenticado, construido o abierto una app desde este caso.
El nuevo commit es c41378acc0c1f24f6534011f1aab09691a9a6948; run41852070764,
intento2. Jobs actuales: macOS126662343529 en cola, Ubuntu126662347435 aprobado
con resultado retenido del intento1; Ubuntu no se volvió a ejecutar. El intento1
falló porque macOS nunca adquirió runner hospedado, con la anotación exacta
«The job was not acquired by Runner of type hosted even after multiple attempts»
y tres avisos de capacidad macOS ARM64. Root relanzó sólo el job fallido mediante
--failed; ci-attempt-history.json conserva ese fallo separado. El intento2 aún
no acredita CI completa verde y el artefacto sigue ausente.
Faltan la CI completa exacta terminada y aprobada, sus dos jobs,
el artefacto nativo nuevo y la revisión/congelación final de root. Los valores
null y el estado de admission hacen que prepare rechace esta plantilla.
No ejecutar comandos de ejemplo hasta sustituirlos y completar todos esos gates.

G es un caso distinto, con source-g, state-g, launcher-home-g, launcher-tmp-g y
Jarvis-G.app nuevos. E conserva su pregunta ausente como fallo; F conserva su
infracción del límite absoluto de128MiB y la señal enviada a la app conocida.
No se amplían ni repiten esos casos. No se modifican el repo ni scripts, recibos o perfiles E/F; la imagen compartida
recibirá sólo destinos nuevos G durante la futura preparación autorizada.
El producto sigue siendo bundle de desarrollo con dependencias externas; no
acredita distribución autocontenida ni medios/audio por faltar soundfile.

Antes del freeze, root debe:

1. Verificar el SHA completo de prepare/supervise/probe. Completar admission
   con jobs macOS+Ubuntu, artifact
   ID/nombre/digest/tamaño observado y bundle ID del nuevo CI. No usar el bundle
   completo de299MB; sólo jarvis-native-qa-SHA, de unos7.7MB.
2. Recalcular los source_pins contra el árbol exacto, incluido codex.py con
   features.apps=false, features.plugins=false y features.remote_plugin=false.
   Revisar diff/AST sin importar ni ejecutar scripts; actualizar scripts_sha256
   en checklist/admission, CI_gate/source_pins de checklist y su hash en admission.
3. Cambiar admission.status a prepared_not_executed y review_status a
   frozen_NOEXEC_root_reviewed. Crear root-review.json con admission_sha256,
   source_commit y execution_review_passed=true, sólo después de revisar el
   artefacto y observar la CI completa verde de ese mismo commit.
4. Conservar el hash final de admission y no cambiar scripts ni pines después.

La preparación tiene300s y permite un attach normal de la misma imagen física
E, sin crear otra cuota. Pinea F prepared/closed/active, el cleanup normal root
y el diagnóstico metadata-only. Reobserva sus cuatro identidades exactas antes
y después, la imagen y el mountpoint por inode, y la lease F inode11385 libre.
No lee bases/historial/credenciales de los perfiles anteriores.

El proveedor es únicamente el sintético E autenticado por el dueño mediante el
cliente oficial. El harness observa metadatos; no lee ni copia auth.json ni
contenido privado. G declara baseline170102784 bytes +64MiB de crecimiento,
con techo absoluto237211648 bytes (226.22265625MiB), muestreado como máximo cada
segundo, dentro de la misma cuota APFS física de2GiB. No es el presupuesto F.
Mantiene Data libre>=25GiB, caída no contabilizada<=1GiB y<=10000 entradas de
metadatos del proveedor. Si falla, no repetir ni ampliar el caso.

Las dependencias y Codex públicos conservados se verifican byte a byte contra el
manifiesto F/E, cambiando sólo el dispositivo del remount y preservando inode,
tamaño, tiempos, modo y hash. source-g contiene sólo src versionado del HEAD
nuevo, cada archivo cotejado con su blob Git exacto. No se instalan dependencias.
El artefacto nuevo se descarga sólo dentro de la cuota; se validan SHA/run/CI,
provenance, los cuatro archivos, __TEXT y codesign. Su carpeta se llama
Jarvis-G.app sin cambiar ni resignar sus archivos. El Jarvis.app anterior queda
preservado.

Oráculo complementario previsto para root, sin cambios de scripts: después de
preparar G y ANTES del único lanzamiento, hacer sólo lstat de los dos archivos
conocidos del mismo proveedor sintético:

- client/cache/remote_plugin_catalog/9c6e5a9fad20d28f.json
- client/.tmp/plugins/.git/objects/pack/pack-c862b02aa1d0540db8b7f5542a4661fc3707a014.pack

Guardar cache-before.json con inode, tamaño, mtime_ns, ctime_ns y bloques
asignados, sin contenido ni auth. Repetir en cache-after.json tras completarse
el mismo run conocido y limpio, antes de Quit; comparar esos metadatos sin
cambios. No añade inferencia, efecto ni recorrido de archivos. Es evidencia
complementaria de esos dos archivos en caché, no condición del aislamiento UI
ni prueba de ausencia universal de actividad de catálogo. Esta observación aún
no se ha realizado.

Plantillas posteriores al freeze, desde el repo (reemplazar ADMISSION_SHA y,
después de preparar, PREPARED_SHA por valores reales):

```sh
.venv.nosync/bin/python -I -S -B -c 'import runpy,sys;sys.path.insert(0,"/private/tmp/jarvis-product-background-native-20261104-g");runpy.run_path("/private/tmp/jarvis-product-background-native-20261104-g/prepare.py",run_name="__main__")' --admit-preparation --admission-sha256 ADMISSION_SHA
.venv.nosync/bin/python -I -S -B -c 'import runpy,sys;sys.path.insert(0,"/private/tmp/jarvis-product-background-native-20261104-g");runpy.run_path("/private/tmp/jarvis-product-background-native-20261104-g/supervise.py",run_name="__main__")' --admit-launch --prepared-sha256 PREPARED_SHA
```

Mantener supervisor activo. Sólo después de active.json adquirir CUA para la
nueva app, comprobando PID/nacimiento/ruta exacta Jarvis-G.app y bundle ID nuevo.
Nunca readquirir handles de E/F. El dueño conserva contraseñas/auth/TCC. Si el
cliente oficial muestra desconectado, detener G para decisión del dueño.

Crear sólo A y B vacíos; A enabled=true/silenced=true revisión2; B ambosfalse
revisión0. No solicitar TCC, permiso de avisos ni notificación real. Enviar en A
exactamente el model_prompt de admission UNA vez y seleccionar B inmediatamente
en la misma llamada CUA. No aceptar herramientas, preguntas ni respuestas host.
Si termina demasiado pronto, falla o no permite la observación, conservarlo y no
iniciar otro run.

La evidencia elegida exige:

1. Captura nativa real B vacío/controles0 mientras A dice «En curso» en sidebar,
   pareada con probe during firmado de sólo lectura: mismo A realmente running,
   cleanup_confirmed=false, B vacío y cero recibos de herramientas/interacciones.
2. Tras ese mismo run completed/known/cleanup=true/answer_source=model, captura B
   todavía vacío y probe after. El probe verifica un único intercambio user/assistant propio de A; no usa la
   fidelidad numérica del texto como condición. El prompt sólo da tiempo para
   observar la UI; el probe nunca emite la respuesta.
3. Volver a A y observar su intercambio y controles1. RPC no sustituye UI nativa.

```sh
.venv.nosync/bin/python -I -S -B -c 'import runpy,sys;sys.path.insert(0,"/private/tmp/jarvis-product-background-native-20261104-g");runpy.run_path("/private/tmp/jarvis-product-background-native-20261104-g/probe.py",run_name="__main__")' MODO --prepared-sha256 PREPARED_SHA --receipt /private/tmp/jarvis-product-background-native-20261104-g/RECIBO.json
```

inventory descubre IDs sin mutar; before/during/after usan --session-a ID_A
--session-b ID_B. during puede descubrir el único run; after debe usar su mismo
--run-id. Cada recibo es nuevo. Probes40s, RPC agregado<=262144bytes,
recibo<=65536bytes; no inferencia ni mutación ni contenido privado exportado.

Para evitar que el modelo complete durante el viaje de ida y vuelta de CUA,
root puede iniciar el modo opcional watch-during justo ANTES de Send+seleccionar
B. Usa la misma plantilla de probe con modo watch-during y los IDs A/B fijos.
Tras conectar y validar, imprime sólo watch_ready con fecha; root puede esperar
ese marcador antes de su única llamada CUA que envía y selecciona B.

El observador espera el único run A hasta20s y100 polls como máximo. Después
conserva hasta16 muestras activas separadas por al menos0,2s, o se detiene al
observar un estado terminal. Un solo cliente/Inspector acumula toda la entrada
RPC, incluidos los polls iniciales, bajo262144bytes; mantiene SIGALRM global40s,
ventana asíncrona30s y un único recibo final<=65536bytes. No reinicia el contador
por muestra y no inicia inferencia ni mutaciones. Sólo conserva preferencias,
inventarios/contadores, estados/condiciones y fechas normalizados, nunca textos
ni valores de respuesta. Las muestras quedan en samples, con selected_run_id y
run_request_started_at_unix para la correlación; run_observed_at_unix sólo
describe cuándo volvió la lectura. Las condiciones fallidas se conservan;
una excepción o un límite no autoriza repetir el modelo o el efecto.

Root debe elegir una muestra real pasada con A_running=true/B vacío cuya
run_request_started_at_unix sea igual o posterior a la fecha real en que CUA observó B
vacío. Se usa el inicio de solicitud, anterior al checkpoint y envío RPC,
para no atribuir a después de B una respuesta que pudo llegar antes.
Sin tal muestra, falla el paso de UI pareada aunque el recibo contenga
otras muestras running anteriores. GUI_temporal_pairing_required sigue true y
ui_selection_proven_by_RPC sigue false: RPC no prueba la selección gráfica.
Los modos inventory/before/during/after permanecen disponibles y la fidelidad
numérica del modelo continúa fuera de las condiciones.

Antes de810s activos, registrar intención y enviar ⌘Q por CUA sólo a la app
exacta ya admitida. Después completion_summary, sin readquirir la app cerrada.

```sh
.venv.nosync/bin/python -I -S -B -c 'import runpy,sys;sys.path.insert(0,"/private/tmp/jarvis-product-background-native-20261104-g");runpy.run_path("/private/tmp/jarvis-product-background-native-20261104-g/supervise.py",run_name="__main__")' --request-GUI-quit --prepared-sha256 PREPARED_SHA
```

El cierre reserva90s: todas las identidades conocidas ausentes y misma lease G
libre antes de hasta dos detach normales. Camino satisfactorio sin señales,
force ni borrado. Deadline/recurso sólo autoriza SIGTERM de la app exacta, nunca
patrones ni identidad incierta. Un fallo de recurso puede conservar custodia y
requerir reconciliación normal explícita de root; no se convierte en aprobado.
Se conservan imagen, perfiles E/F/G y evidencia. No exportar proveedor, auth,
claves ni bases de datos. No ejecutar build/suites completas en este Mac.
