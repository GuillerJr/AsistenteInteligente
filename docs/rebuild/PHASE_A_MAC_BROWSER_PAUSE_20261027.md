# Pausa solicitada por el dueño — 27 de octubre de 2026

> **Registro histórico: reanudado expresamente por el dueño.** El contenido de
> abajo conserva el estado al pausar; sus PID y pasos pendientes no son el estado
> actual. La continuación y sus oráculos se registran en el
> [cierre del bloque](PHASE_A_MAC_BROWSER_CLOSEOUT.md). El destino antiguo salió;
> la prueba nueva usó otro proceso y no heredó su permiso ni repitió su press.
> Las acciones y los recorridos de persistencia/revocación/rechazo del bundle 04
> se completaron tras reanudar; la publicación se registra por separado.

El dueño pidió pausar por hoy y continuar mañana. Agentes interrumpidos; no avanzar ni publicar hasta que reanude. No hay objetivo automático ni recordatorio programado.

## Git y alcance

- Repositorio: `/Users/guillerjr/Documents/Proyectos/AsistenteInteligente`.
- Sólo `main`; HEAD de partida `5e236de61c09cca2b8adff6e299382fc971b3ee1`.
- **Todos los cambios siguen sin commit ni push**. No se tocó la app instalada ni perfiles personales.
- Continúa el bloque Mac/navegador de A; B sigue aplazada. Investigación REA parcial: 27 lotes, 105 rutas TS/JS. No afirmar completa la fase A.
- El dueño delegó aprobaciones de perfiles sintéticos y, en respuesta posterior, la manipulación puntual de TCC. Él completó la autenticación de macOS; nunca se leyó ni registró contraseña.

## Evidencia que ya pasó

- Python final: **2672 aprobadas**, 488.93 s, `/private/tmp/jarvis-mac-browser-python-final-full.log`. Python no cambió después.
- Swift: **392 casos, 1 omitido, 0 fallos**, 154.796 s, `/private/tmp/jarvis-mac-browser-swift-final-full.log`. Esta suite fue **antes** del último arreglo de postcondiciones AX.
- Último arreglo AX: **33 focales aprobados**; `/private/tmp/jarvis-mac-ax-postcondition-tests.log`. Press/scroll despachan una vez y releen la postcondición explícita incluso si falta ACK. `native_acknowledged` queda separado del estado observado. No se cambió el timeout ni se reescribió el recibo histórico incierto.
- Build04 aprobado, 45.88 s; `/private/tmp/jarvis-mac-browser-build-04.log`.
- Manifiesto fuente v2: **356 archivos**, `/private/tmp/jarvis-mac-browser-final-source-manifest-v2.json`, SHA-256 `30ffc5274692415f79531d86c6de0efd135346f0ef26720714518cd24f5a54f2`. Copia documental en `PHASE_A_MAC_BROWSER_SOURCE_MANIFEST.json`; conserva también 355 y 173 identidades históricas.
- Chrome real optativo aprobado aparte. QA02: 55 propuestas/respuestas únicas; navegador administrado y CDP real, búsqueda explícita, HTTPS público con proxy, descarga, patch y PTY reales. No es inferencia autenticada: SDK de prueba propio.
- AX entre procesos: texto Unicode, selección y salto de línea corroborados por oráculo externo; frame real de ventana de **14300 bytes**, SHA-256 `bf4463d26c31bfd1cd61360093d84d35b25dc89abcc4ee0d8ea42905c1cdde0c`, enviado en RAM al peer, sin portador en SQL ni archivos de captura. Descarga final verificada y `written_to_workspace=true` coherente.
- Un press anterior produjo contador **1** pero recibo `unknown`; se conservó y **no se repitió**. La corrección posterior aún necesita la operación nueva **1→2**.
- «Siempre para este destino» fue seleccionado y confirmado por CUA en el perfil sintético. Funcionó en otro chat sin nuevas aprobaciones ni decisiones RPC. Permiso persistente `a9d2f46228b64d0eadc266b8d3f9257a` para `desktop_observe` del destino externo exacto y `include_frame=false`.
- Tres informes propios preservados en `evidence/mac-browser-20261027/`, sin imágenes/base64 ni claves. El cierre principal distingue resultados y pendientes.

## Punto exacto de la prueba al pausar

App **04** con el último código, firmada y con identidad exclusiva:

- Ruta: `/private/tmp/jarvis-mac-browser-build-04/Jarvis Prueba Mac 04.app`.
- Bundle ID: `com.jarvis.desktop.macbrowserqualification.v04.20261027`.
- CDHash: `d0fbc0dcd13bfcef08ddb9583fbbb03c81cc421e`. **No volver a firmar ni modificar el bundle** si existe y coincide.
- Accesibilidad y pantalla se añadieron por UI a esta ruta exacta. La última vista de Ajustes mostraba «Es posible que Jarvis Prueba Mac 04…» con «Más tarde» / «Cerrar y volver a abrir». El reinicio y la efectividad de TCC04 **todavía no se comprobaron**. Revisar esa vista filtrando sólo el permiso; no mostrar el árbol completo de Ajustes.
- Primero elegir «Más tarde» si sigue el aviso, salir de 04 limpiamente y confirmar cierre; reabrir con los argumentos explícitos de abajo. No usar el reinicio automático que pierda esos argumentos.
- La copia anterior reutilizaba un identificador con otro cdhash: toggles activados no acreditaban permiso del nuevo proceso. La identidad fresca anterior sí pasó AX y frame. No tocar entradas ambiguas del Jarvis personal.

Runtime privado (evita el bloqueo de lectura observado bajo Documentos):

```bash
open -n '/private/tmp/jarvis-mac-browser-build-04/Jarvis Prueba Mac 04.app' --args \
 --python /opt/homebrew/Cellar/python@3.11/3.11.15_1/Frameworks/Python.framework/Versions/3.11/bin/python3.11 \
 --source /private/tmp/jarvis-mac-browser-source-final-20261026/src \
 --dependencies /private/tmp/jarvis-mac-browser-runtime-20261027/site-packages \
 --state /private/tmp/jarvis-mac-browser-live-20261026/profile \
 --codex /private/tmp/jarvis-mac-browser-live-20261026/jarvis-mac-browser-peer \
 --automation-test-origin http://127.0.0.1:59399
```

No es distribución autocontenida. La importación desde Terminal pasó, pero un hijo GUI con el Python/dependencias bajo Documentos se bloqueó en opendir antes de crear socket/SDK; se cerró sin efectos. Se conservaron muestra y diagnóstico en `/private/tmp/jarvis-mac-browser-service-03*`.

## Destino externo y continuación

- Fixture propio: `/private/tmp/jarvis-mac-external-fixture-20261027/JarvisMacExternalFixture.app`, bundle `com.jarvis.fixture.macexternal.20261027`.
- Oráculo: `state.json` en esa raíz; contiene sólo texto artificial, contador y selección. Al pausar: PID **27924**, contador **1** y texto `INICIO 🧪\nSEGUNDA LÍNEA + AÑADIDO\nTERCERA`, selección UTF-16 `[41,0]`.
- Ventana `Documento externo sintético Jarvis`; window_ref `beeaf0760b008457efc59765e2004612eeaa5b2a34197f19d35ee8e3617f0e30`; launch_id `604a22b181c41fd3253bdea782e67fa0f66f009edbcce8a3dffe3f00a780b00b`. **Revalidar** antes de usar; los PID/refs pueden caducar. Mantener esa ventana permite probar persistencia del permiso al reiniciar Jarvis. Si el Mac reinició, no fingir que la referencia anterior sigue válida.
- Driver privado preparado: `/private/tmp/jarvis-mac-browser-rpc-qa03-20261027.py`; SHA-256 `e7d997e43c4f92346940bf925e552165041ebb64c458e1b0280ac1e9bd347055`. **No ejecutar otra vez qualify completo**: el estado ahora es contador1; preparar operación nueva explícita 1→2 y sus refs frescas.
- Sesión principal: `c1a7690a375a45489abac9a2d17e1e7f`.
- Queda: confirmar TCC04; nueva pulsación 1→2 con postcondición `CONTADOR 2` y oráculo; frame/revalidación final; persistencia «Siempre» tras reinicio; revocación por CUA y rechazo de una nueva solicitud. Después actualizar cierre/manifiestos, controles, commit y push a main y CI. No empezar B.
- Servidor HTTP sintético: sesión exec **97225**, PID histórico14948, origen59399. Chrome propio: supervisor exec **32365**, PID histórico16583, endpoint59498, target `C966A34C56B46F77940A184382CAC11D`. No enumerar ni tocar navegador del dueño. Comprobar identidad/liveness antes de reutilizar o cerrar procesos.
- La hoja de reinicio puede seguir abierta; las apps/fixtures de prueba pueden continuar vivos. No hay acciones nativas en curso ni aprobaciones pendientes dentro de Jarvis. Los tres agentes del bloque fueron interrumpidos al pausar.
