# Verificación del lote de pendientes de Jarvis 1.0

Fecha: 21 de octubre de 2026. Continúa los 23 puntos abiertos tras `b6ab413`.
La lista autoritativa de estado es [V1_PENDIENTES.md](../V1_PENDIENTES.md).

## Cobertura añadida

- Historial cifrado: bifurcación al editar/regenerar, valoración por respuesta,
  archivado separado de la papelera y recuperación que conserva el borrador.
- Scheduler y Telegram: aprobación pendiente con vencimiento, aviso para revisar,
  separación de callbacks de voz y vínculo entre dueño, chat, mensaje y digest.
- Bienvenida: primera ejecución, credencial ausente, guardado, estado del servicio,
  carreras con voz/aprobaciones/grabaciones, envío rechazado y recuperación.
- Distribución: versiones y licencias, rutas relativas al bundle, SMAppService,
  canal HTTPS fijo, firma Ed25519, límites, downgrade y revalidación de inactividad.
- VAD: PCM sintético con ONNX real, reinicio, presupuesto por trama, timeout,
  política de certificado e identificador mediante fixtures y rechazo de sustituciones/symlinks. La comprobación positiva de la firma instalada se hace con `--vad-self-test`.
- Red local: 34 casos de denegación en sockets, SSDP, Wake-on-LAN y TCP.
- Descubrimiento de televisores: 66 regresiones adicionales del enrutado, grafo
  NVIDIA, broker y handler reales con transporte simulado. La petición explícita
  de descubrimiento local deja de acabar en búsqueda web; no enciende ni controla
  dispositivos. Las instrucciones compuestas piden aclaración, las compras siguen
  usando búsqueda web y los controles conservan confirmación. La respuesta valida
  llamadas, direcciones y plataformas, y omite el texto no confiable de SSDP.
  La misma validación se aplica cuando NVIDIA propone la llamada; las combinaciones
  no soportadas no pasan anuncios de red al sintetizador.
  Las 463 pruebas enfocadas pasan sin red real.
- Interacción con apps: la petición explícita de escribir en TextEdit conserva
  el destino visual, aunque el selector, una habilidad o el contexto propongan
  crear un archivo en Drive. Las regresiones usan el grafo NVIDIA y el broker
  reales, con proveedores sintéticos y sin ejecutar acciones en la cuenta.
  La aprobación conserva la petición completa y sus restricciones; el ejecutor
  exige el literal solicitado y comprueba la escritura mediante observaciones AX
  sintéticas. Una restricción de no abrir otros documentos no invalida la edición
  solicitada en el documento nuevo ya abierto.
- Permisos por app: se valida y consume la aprobación antes de guardar el permiso,
  dentro de la misma sección crítica y antes de reanudar la acción. Las regresiones
  cubren cancelación, caducidad, digest inválido, error de escritura y revocación
  concurrente, sin conceder permisos ni ejecutar acciones en la cuenta del dueño.
- Propuestas visuales denegadas: tres regresiones del grafo comprueban destino,
  literal y campo inválidos. Se conservan las denegaciones del broker y no se
  ejecuta ninguna herramienta ni se llama al sintetizador. La respuesta visible
  indica que no se ejecutó la acción, sin promesas ni argumentos rechazados.
- Percepción de TextEdit: se reproduce el valor AX «Borrar» de los controles de
  color del documento vacío. Esos botones se omiten del contexto, pero siguen
  prohibidos como destinos de acciones. Las pruebas conservan el bloqueo de
  credenciales, pagos y permisos, incluidas etiquetas con diacríticos y nodos
  privados sin texto exportable, geometría o rol admitido. El OCR sigue siendo
  conservador y los descendientes de los botones omitidos se inspeccionan.
- Permisos de apps por IPC: pruebas con firma ECDSA, conversación temporal,
  grafo, broker y gestor reales verifican conservación tras bloqueo/cancelación,
  aislamiento entre chats, persistencia de «Siempre» y revocación. La firma de
  una aprobación puntual no sirve para conceder un permiso por conversación.
  El controlador recibe la petición exacta de TextEdit con observaciones sintéticas:
  escribe solo el literal solicitado y bloquea contenido sensible antes de llamar
  al modelo visual. La interfaz solo confirma un permiso guardado cuando recibe
  una respuesta positiva al método firmado correspondiente. Si Touch ID o voz
  consumieron la aprobación mientras estaba abierto el menú de alcance, aparece
  un aviso de permiso no confirmado; no se concede después por esa vía. Las
  regresiones usan el flujo real del modelo de interfaz, sin IPC ni credenciales,
  y comprueban que no modifica otra conversación ni una sesión bloqueada.
- Errores: fronteras de excepción con solo `error_type`, privacidad y cancelación.
- Grafo productivo: las suites vigentes importan directamente el factory NVIDIA;
  las regresiones del cerebro retirado lo identifican expresamente como histórico.
  La comprobación conjunta de 26 archivos pasa sus 818 casos sin `PYTHONPATH`.
- Daemon autónomo: el manifiesto incluye los módulos de memoria cargados de forma
  diferida. Una regresión comprueba cada exportación pública y el ejecutable
  congelado supera su autoprueba sin red, sockets ni escrituras persistentes.
- Interfaz: Enter sobre el campo real del navegador, teclado y cierres sin acciones,
  catálogo español con interpolaciones tipadas y vistas con datos sintéticos.

## Resultados comprobados

- Python del lote inicial: 3.934 pruebas pasan y 46 omisiones previstas. Ruff limpio.
- Swift: 371 pruebas del núcleo y 48 XCTest de interfaz pasan, sin fallos.
- Catálogo español: 1.181 claves, sincronizadas con la extracción del compilador.
- SwiftPM nativo: las 371 pruebas del núcleo pasan también con el perfil explícito
  de CI y el auxiliar ONNX real. Una compilación sintética comprueba la extracción
  de interpolaciones a una ruta con espacios; los builds normales no intentan
  escribir archivos de localización en la raíz del disco.
- Paquete nativo Release de prueba, sin daemon: firma ad hoc con Hardened Runtime
  y verificación estricta válida; ejecutable Jarvis 5,32 MiB,
  VAD 19,82 MiB y ONNX/modelo Silero una única vez. Los demás auxiliares
  quedan entre 0,12 y 1,98 MiB.
- VAD real, 100 tramas PCM sintéticas: media 0,205 ms, p95 0,244 ms,
  máximo 1,003 ms frente a 32 ms de audio por trama.
- Contratos deterministas: aceptación 25/25, producción 20/20 y resistencia
  prolongada 400 ejecuciones, con 1.160/1.160 comprobaciones y cero tareas filtradas.
- Sin `jarvis-local-brain`, motores MLX/VLM ni Cmlx en ese paquete de prueba,
  incluso al reutilizar una caché que anteriormente los contenía.
- Instalación local firmada completa: diagnóstico `jarvis_daily_beta` aprobado,
  con revisión exacta y árbol limpio, daemon supervisado, IPC seguro y NVIDIA
  configurado. El VAD instalado supera `--vad-self-test`. Ejecutable principal:
  5.599.936 bytes (5,34 MiB); bundle completo: 317.156 KiB, bajo su presupuesto.
- Traslado real a `/Applications`: firma estricta válida, VAD correcto y arranque
  del daemon desde la copia, con readiness reciente y seguridad intacta. Al terminar
  se retiró únicamente la copia de prueba y se restauró la instalación habitual.
- CI remoto con la corrección de SwiftPM: Python y Swift aprobados en GitHub
  para `926071f`, incluido el runner con Xcode 26.
- Bienvenida en cuenta nueva: el dueño confirmó que completó el flujo y recibió
  la primera respuesta dentro de «Jarvis Pruebas». La copia de `ff0e7be` se compiló
  sin modelos personales de voz y superó firma estricta y VAD sintético antes de
  compartirla con esa cuenta. La ejecución en la cuenta nueva fue una comprobación
  manual reportada por el dueño; el control de Codex seguía en la sesión habitual.
  El aviso y la denegación de red local se comprobaron posteriormente en la cuenta
  principal, como se detalla a continuación.
- Regresión de televisores en `0262a63`: 4.000 pruebas Python aprobadas y 46
  omisiones previstas; 371 pruebas Swift del núcleo y 48 XCTest de interfaz,
  paquete Release y contratos de aceptación, producción y fiabilidad aprobados.
  El CI remoto de esa revisión también terminó correctamente.
- Reinstalación firmada de `0262a63` en la cuenta principal: revisión exacta,
  firma estricta completa, `jarvis_daily_beta` y VAD sintético aprobados. App y
  daemon se ejecutan desde `~/Applications/Jarvis.app` en `guillerjr`.
- Red local real: en un chat temporal con el historial oculto, Codex envió la
  petición explícita de descubrimiento. El dueño confirmó el aviso de macOS y
  que pulsó «No permitir»; esa decisión fue suya. Codex observó la respuesta
  «No pude completar el descubrimiento de televisores en la red local. Revisa
  la conexión y el permiso Red local de Jarvis en Ajustes del Sistema.» y,
  a continuación, «El resultado es 4» al preguntar la suma. Se verificó además
  el texto español de `NSLocalNetworkUsageDescription` en el bundle instalado.
  Esta prueba se hizo en la cuenta principal por indicación del dueño, sin
  reiniciar permisos ni usar la cuenta de pruebas.
- Inicio de la prueba de permisos por app: la petición de escribir en un documento
  nuevo y vacío de TextEdit propuso incorrectamente crear un archivo en Drive.
  Se rechazó desde la interfaz y Jarvis confirmó que no ejecutó la acción. Las
  comprobaciones en vivo de «En esta conversación», «Siempre» y revocación
  permanecen abiertas hasta verificar la app corregida con las decisiones del dueño.
- Corrección de apps `ad53037`: 4.090 pruebas Python pasan y 46 se omiten;
  371 pruebas del núcleo Swift y 48 de interfaz, paquete Release, aceptación,
  producción y resistencia prolongada aprobados. CI remoto aprobado. La revisión
  instalada coincide exactamente; firma estricta, diagnóstico completo y VAD
  sintético pasan en la cuenta principal. En la prueba de TextEdit, el modelo
  respondió con opciones en el chat sin una llamada; se añade la exigencia de
  propuesta nativa y una regresión para impedir esa confirmación simulada.
- Propuesta nativa `06025bb`: 4.093 pruebas Python pasan y 46 se omiten;
  371 pruebas Swift del núcleo y 48 de interfaz, paquete Release, aceptación,
  producción y resistencia prolongada aprobados. CI remoto aprobado. Instalación
  firmada en la cuenta principal con revisión exacta, firma estricta,
  `jarvis_daily_beta` y VAD sintético correctos. La aprobación real de TextEdit
  conserva el objetivo y el campo solicitado. Hubo caducidades sin ejecución y
  una detención por intervención del teclado o puntero; el documento seguía vacío.
  Un reintento emitió una promesa sin actuar. Se reprodujo sintéticamente una vía
  que permite ese síntoma después de una propuesta denegada, sin atribuirle el
  motivo exacto del turno real. Se corrige la respuesta de esa vía; el recorrido
  manual de permisos sigue abierto.

- Revisión `fc4b4f6`: 4.096 pruebas Python y 419 Swift aprobadas, junto con el
  paquete Release y los contratos; CI remoto aprobado. Instalación firmada en
  la cuenta principal con revisión exacta, firma estricta, VAD sintético y
  `jarvis_daily_beta` correctos. El dueño eligió «En esta conversación»; Jarvis
  se detuvo por contenido sensible y el documento quedó vacío. Al repetir la
  petición volvió a pedir aprobación y se rechazó. El falso positivo de los
  controles de color se reproduce sintéticamente; no se atribuye una causa
  demostrada a la repetición de la aprobación. El nuevo aviso permite comprobar
  el alcance efectivamente guardado en el siguiente recorrido real.
- Revisión `b442202`: batería completa Python, 379 pruebas del núcleo Swift y
  54 de interfaz aprobadas; paquete Release, aceptación, producción y fiabilidad
  aprobados. CI remoto aprobado. Instalación firmada exacta, diagnóstico y VAD
  correctos; ejecutable de 5.600.000 bytes, bajo el presupuesto de 25 MiB.
  El aviso confirmó un permiso permanente seleccionado por el dueño y TextEdit
  recibió exactamente «PRUEBA JARVIS A». El resultado se declaró parcial porque
  no se verificó la postcondición. Una regresión reproduce la etiqueta AX compuesta
  «First Text View área de texto» frente al nombre exacto solicitado; la corrección
  conserva por separado una etiqueta estable y el descriptor completo de seguridad.
  No se aceptan coincidencias aproximadas ni se repite una escritura sin verificar.
  La petición de «añadir» reproduce además una omisión de ese infinitivo en la
  validación de postcondiciones. Se corrige solo ese verbo: una prueba conserva
  el contenido previo y las restricciones, y la variante «no añadir» sigue
  rechazándose antes de consultar el modelo visual o ejecutar acciones.
  En otra conversación se observó una propuesta denegada y luego una respuesta
  incompleta de NVIDIA, sin segunda escritura. Sigue pendiente demostrar la
  reutilización completa, la revocación y el alcance por conversación en vivo.

## Inspección visual reproducible

```bash
AEGIS_UI_EVIDENCE_ROOT=/private/tmp/jarvis-v1-ui-evidence ./script/test_native.sh
```

`JarvisVisualAccessibilityTests` genera ocho PNG de contenido sintético:
chat con navegador a 800 px, Ajustes mínimos, popover y aprobación, cada uno en
claro y oscuro. Se revisaron las ocho capturas. Los bloques largos de Ajustes son
desplazables; el historial también desplaza proyectos y pendientes y conserva su
pie. Al faltar ancho, el navegador/chat lateral oculta el historial.
Las capturas no contienen historial del dueño ni pantalla del escritorio.

La inspección del árbol AX y las pruebas de teclado no equivalen a una certificación
auditiva completa con VoiceOver. Ese recorrido sigue declarado en P1-12.

## Comprobaciones reproducibles

```bash
.venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/python -m pytest -p no:warnings
./script/test_native.sh
.venv/bin/python script/sync_localizations.py --check
./script/menu_bar_service.sh install
./script/jarvis_beta.sh check
"$HOME/Applications/Jarvis.app/Contents/MacOS/Jarvis" --vad-self-test
```

`--vad-self-test` procesa silencio sintético desde la app instalada y exige la
identidad firmada del auxiliar. Sale antes de iniciar daemon, micrófono o migración
de inicio de sesión. No concede permisos ni aprueba acciones.

Los hooks vuelven a ejecutar las pruebas, el paquete Release y los contratos de
aceptación, producción y fiabilidad antes del push. No se debe editar el árbol ni
instalar durante el pre-push.

## Certificaciones externas pendientes

- Developer ID, perfil de notarización y publicación aprobada del canal estable;
  prueba real de actualización 1.0.0 → 1.0.1 notarizada.
- Aprobación explícita «Siempre» para una app y posterior revocación en la interfaz.
- Recorrido auditivo completo con VoiceOver, mediante validación humana.
- Pruebas físicas de voz del alcance P3.

La implementación y sus pruebas no sustituyen esas decisiones personales. No se
marca una publicación notarizada, permiso o aprobación real como realizado mediante
un fixture.
