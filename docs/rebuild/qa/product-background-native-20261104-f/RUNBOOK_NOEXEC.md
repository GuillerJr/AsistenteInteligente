# Caso F: aislamiento de B mientras A está en ejecución

Este borrador no se ha ejecutado. Es un caso nuevo y acotado; no repite el turno
de E, no amplía su plazo y no convierte en aprobada su pregunta ausente.
Se heredan de E el supervisor, los límites, la custodia por identidad completa y
el cierre normal. El producto y los cuatro archivos de la app CI no se modifican.
La app sigue siendo un bundle de desarrollo, no distribución autocontenida.

Raíz: `/private/tmp/jarvis-product-background-native-20261104-f`.
Admission SHA: `6c3f0f32ed93dcdeacfa124f11b79573e661bd2531db29240ad9ea1e9f458546`.
Checklist SHA: `7c467e51e3d6510476b61864306446ce92a023a50e95b4ad1f43cb490f913811`.

Antes de ejecutar, el responsable revisa hashes/AST sin importar los scripts y
registra su revisión. No hace falta descargar, compilar ni volver a ejecutar CI:
el commit 2c8259de8112aa5f1487814a5c5c35b3931e5c43 ya está cualificado.

## Preparación única

Desde el repositorio, ejecutar sólo tras revisión:

```sh
.venv.nosync/bin/python -I -S -B -c 'import runpy,sys;sys.path.insert(0,"/private/tmp/jarvis-product-background-native-20261104-f");runpy.run_path("/private/tmp/jarvis-product-background-native-20261104-f/prepare.py",run_name="__main__")' --admit-preparation --admission-sha256 6c3f0f32ed93dcdeacfa124f11b79573e661bd2531db29240ad9ea1e9f458546
```

Máximo 300 segundos. Comprueba cierre E y ausencia exacta de sus 12 identidades,
imagen/mountpoint por inode y un único attach normal. Sólo readmite dispositivos
de las copias públicas después de verificar los demás metadatos y todos sus
hashes. Comprueba el inode 3334 de la antigua lease y que esté libre; no lee bases
del perfil E. Crea únicamente state-f, launcher-home-f y launcher-tmp-f.
El proveedor sintético E sólo lo usa el cliente oficial: el harness no lee ni
copia auth. Mantiene 128 MiB absolutos, aunque su baseline sea 106471424 bytes.
La referencia de recursos F empieza después de montar y no altera los recibos E.

Un fallo preserva imagen y evidencias. No repetir prepare, attach ni lanzamiento.
No hay limpieza automática del volumen ante custodia incierta. El cierre de un
fallo de preparación debe reconciliarse expresamente por el responsable.

## Lanzamiento y UI

Tomar el hash de prepared.json devuelto como `PREPARED_SHA` en los comandos
siguientes; sustituir el marcador, sin inventar un hash.

```sh
.venv.nosync/bin/python -I -S -B -c 'import runpy,sys;sys.path.insert(0,"/private/tmp/jarvis-product-background-native-20261104-f");runpy.run_path("/private/tmp/jarvis-product-background-native-20261104-f/supervise.py",run_name="__main__")' --admit-launch --prepared-sha256 PREPARED_SHA
```

Mantener ese supervisor activo. Sólo después de active.json, confirmar su app
real por PID, nacimiento y ruta exacta y adquirir CUA para esa app. No consultar
la app del dueño ni readquirir después de Quit o ante un arranque incierto.
El proveedor sintético debería continuar conectado. Si aparece desconectado,
detener el caso para decisión del dueño: no leer ni copiar credenciales.

Crear sólo A y B. A tendrá preferencias revisión 2, enabled=true, silenced=true;
B seguirá revisión 0, ambas false. Ambas deben estar vacías inicialmente.
No solicitar permiso de avisos de macOS ni emitir avisos reales.

En A enviar exactamente una vez el `model_prompt` de admission.json y seleccionar
B inmediatamente dentro de la misma llamada CUA. El prompt pide los números
1 a 1000, una línea cada uno, sin herramientas. Es una instrucción al modelo, no
una allowlist impuesta. Nunca contestar preguntas ni aprobar herramientas en F.
No iniciar otro turno si acaba demasiado pronto o no permite obtener la evidencia.

## Probes de sólo lectura

Plantilla bootstrap para probe.py:

```sh
.venv.nosync/bin/python -I -S -B -c 'import runpy,sys;sys.path.insert(0,"/private/tmp/jarvis-product-background-native-20261104-f");runpy.run_path("/private/tmp/jarvis-product-background-native-20261104-f/probe.py",run_name="__main__")' MODO --prepared-sha256 PREPARED_SHA --receipt /private/tmp/jarvis-product-background-native-20261104-f/RECIBO.json
```

`inventory` descubre sólo los dos IDs sintéticos; `before`, `during` y `after`
añaden `--session-a ID_A --session-b ID_B`. `during` permite omitir `--run-id`
y deriva únicamente el run de A; los posteriores usan ese ID admitido.
Cada recibo debe ser nuevo: no sobrescribir ni repetir una operación incierta.
Los probes no envían prompts, respuestas, autorizaciones ni otras mutaciones.
Cada uno tiene 40 segundos, input 262144 bytes y salida de recibo 65536 bytes.

Se necesitan pruebas pareadas reales:

1. CUA B vacío mientras la barra lateral dice A «En curso», con `during` que
   confirme running, cleanup_confirmed=false, B vacío, preferencias propias y
   ausencia de herramientas/interacciones. RPC no sustituye la captura nativa.
2. Tras completarse ese único run conocido y limpiado, B continúa vacío.
3. Seleccionar A y comprobar que muestra su único intercambio propio.

Los textos de respuesta no se exportan en los recibos RPC. Las observaciones UI
sólo describen estado sintético, sesión seleccionada y contadores; no autentican
una pregunta ausente ni afirman paridad global.

## Cierre

Dentro de los 810 segundos activos, registrar intención de cierre con la misma
plantilla de supervise.py y `--request-GUI-quit --prepared-sha256 PREPARED_SHA`.
Después enviar ⌘Q sólo a la app exacta ya admitida. La siguiente llamada CUA será
su completion_summary, sin readquirir la app cerrada.

El supervisor observa limpieza dentro de sus 90 segundos reservados: todas las
identidades conocidas ausentes y misma lease F libre antes de hasta dos detach
normales, sin force ni borrado. El camino satisfactorio no usa señales.
Una infracción sólo autoriza SIGTERM de la identidad exacta admitida; una
identidad incierta nunca se transforma en autoridad de señal o replay.
Conservar imagen E, perfiles E/F, scripts y recibos. Archivar sólo los documentos
y recibos públicos sanitizados; jamás auth, claves, bases de datos ni el proveedor.
