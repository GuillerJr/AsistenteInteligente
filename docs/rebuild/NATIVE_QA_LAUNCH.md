# Arranque nativo de los perfiles de prueba

El helper `JarvisQALauncher` pertenece al entorno de cualificación. Se compila
sólo en CI, con `JARVIS_BUILD_QA_LAUNCHER=1`, y se conserva en el artefacto de
desarrollo bajo `Contents/Helpers`. El bundle normal conserva su ejecutable
principal `Contents/MacOS/Jarvis`; no se sustituye por un script.

## Incidente conservado

La copia de prueba 201a5b7 declaró `JarvisQA`, un script que ejecutaba el Mach-O,
como `CFBundleExecutable`. Su lanzamiento directo desde Python produjo un
SIGABRT de TCC al solicitar reconocimiento de voz. El informe del PID propio
69587, cotejado con el UUID del binario, no tenía bundle ID y atribuía la operación
a otro proceso. Las descripciones de voz sí existían en el plist del bundle.
El paquete original de CI declaraba correctamente `Jarvis`.

Apple documenta que TCC espera un ejecutable principal Mach-O en los bundles;
un script como principal puede romper esa atribución. Ver
[TCC and Main Executables](https://developer.apple.com/forums/thread/678819).
El intento fallido y el cierre posterior del servicio huérfano se conservan en
[el registro de cualificación](PHASE_A_TELEGRAM_VOICE_MEDIA_QUALIFICATION.json).
No equivalen a una prueba satisfactoria de permisos o cierre normal.

## Contrato del helper

- Admite únicamente un bundle `local.jarvis.*` en una carpeta sintética propia
  bajo `/private/tmp`, con `Jarvis` arm64 Mach-O, huella exacta y descripciones de
  micrófono/reconocimiento. Rechaza el wrapper de shell antes de abrir la app.
- Recibe un JSON de configuración con los seis argumentos de rutas del cliente
  y sólo `HOME`, `TMPDIR` y el `PATH` admitido. El estado, fuentes, dependencias,
  proveedor y temporales se indican de forma explícita; no busca perfiles del dueño.
- Reserva y sincroniza un recibo exclusivo antes de solicitar la apertura.
  [LaunchServices](https://developer.apple.com/documentation/appkit/nsworkspace/openapplication(at:configuration:completionhandler:))
  devuelve la aplicación y su PID; no se adivinan mediante argumentos de otros
  procesos. El helper comprueba identidad, rutas, fecha y proceso devueltos.
- El recibo conserva huellas de argumentos/entorno, no sus contenidos. Un fallo
  posterior al intento conserva incertidumbre y no permite repetirlo usando el
  mismo recibo. No concede TCC ni sustituye la aprobación del dueño.

## Recorrido de cualificación

1. Descargar el artefacto del commit exacto aprobado en CI y cotejar su
   procedencia y huellas. Conservar el original y preparar una copia con nombre
   e identificador sintéticos, ejecutable principal nativo y firma verificada.
2. Confirmar la salida de la app, servicio y procesos de prueba anteriores y la
   liberación del perfil. Conservar sus recibos y originales.
3. Preparar solicitud, recibo nuevo y supervisor externo con presupuesto y plazo.
   Ejecutar el helper del artefacto; un recibo incierto exige revisión, sin
   relanzamiento automático. El helper no supervisa por sí solo toda la sesión.
4. Comprobar primero el aviso de voz y que la app siga abierta. El dueño concede
   TCC y escribe las credenciales; las aprobaciones del perfil sintético están
   delegadas al agente. Después continuar los recorridos reales pendientes.
5. Cerrar desde Jarvis y comprobar los procesos custodiados, el bloqueo del perfil
   y el recibo de recursos. No inferir un cierre normal de una señal o de la mera
   desaparición de la ventana.

Las pruebas del contrato utilizan un driver y archivos sintéticos: acreditan
admisión, identidad, recibos y ausencia de repetición. El aviso real de macOS y la
integración de voz se acreditan por separado desde la app.
