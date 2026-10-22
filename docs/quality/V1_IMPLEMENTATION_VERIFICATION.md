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

- Python: 3.934 pruebas pasan y 46 omisiones previstas. Ruff limpio.
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
- Cuenta limpia de macOS, clave NVIDIA introducida por el dueño y decisiones TCC
  para certificar bienvenida y aviso/denegación de red local.
- Aprobación explícita «Siempre» para una app y posterior revocación en la interfaz.
- Recorrido auditivo completo con VoiceOver, mediante validación humana.
- Pruebas físicas de voz del alcance P3.

La implementación y sus pruebas no sustituyen esas decisiones personales. No se
marca una publicación notarizada, permiso o aprobación real como realizado mediante
un fixture.
