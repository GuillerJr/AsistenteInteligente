# Regresión de arranque de los fixtures nativos — 27-10-2026

La CI del checkpoint `38a27d1` aprobó Python y Ubuntu, pero sus dos casos de
`NativeAutomationIntegrationTests` fallaron con `startupUnconfirmed` después de
unos ocho segundos. [Trabajo de macOS](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40520473626/job/122245871222).
No era otro agotamiento del presupuesto global de 35 minutos.

## Dependencia identificada y reproducción

El fixture construía `http.server.ThreadingHTTPServer` antes de crear
`JarvisService`. Su `HTTPServer.server_bind` llama sincrónicamente a
`socket.getfqdn`, incluso para la dirección numérica `127.0.0.1`.
Por ello el proceso podía seguir vivo sin haber publicado el socket RPC.
El log remoto no captura la pila del resolver: no se atribuye el retraso a un
servidor DNS concreto ni se afirma haber observado esa consulta histórica.

Las dos pruebas originales pasaron localmente en 4,333 s. Se añadieron dos
regresiones que sustituyen únicamente el resolver del hijo sintético:

- DNS bloqueado durante nueve segundos: antes de la corrección reprodujo
  `startupUnconfirmed` en 8,688 s.
- DNS no disponible: antes de la corrección el hijo salió y se conservó
  `serviceExited`, en 0,315 s.

## Corrección propia y límites

`LoopbackHTTPFixture` prepara explícitamente los scripts de los cinco fixtures
HTTP nativos. Su servidor exige IPv4, el literal `127.0.0.1` y un puerto entero
válido; usa el bind TCP normal y conserva el puerto asignado. Fija el nombre
numérico sin consultar DNS. Incluye el listener señuelo, CDP, Chrome, auditoría
y la integración del worker firmado.

No modifica el arranque productivo, sus plazos, autenticación, TCC ni la política
de red del navegador. Tampoco modifica globalmente la clase HTTP de la stdlib.
Las regresiones exigen salud RPC, origen loopback y cero inferencias, solicitudes
HTTP o claims de herramientas durante el arranque, sin marcador de consulta DNS.

Ante otro fallo de preparación, el fixture cierra su propio hijo y muestra como
máximo los últimos 8 KiB de su stderr sintético antes del teardown. Un fallo al
leer ese diagnóstico no sustituye el error original.

## Barreras de cancelación en las pruebas Python

El primer pre-commit de esta corrección detectó otra carrera en
`test_activation_management.py`: la espera de una notificación mediante
`asyncio.to_thread` podía ocupar el mismo executor que debía publicar el efecto.
Un executor de un hilo reprodujo tanto el fallo de publicación como el del
fixture hermano de limpieza antes de cambiar su coordinación.

Ambas notificaciones pasan ahora por `asyncio.Event` y
`loop.call_soon_threadsafe`. La espera es asíncrona, tiene un presupuesto finito
y también observa una terminación prematura del worker. El gate se libera siempre.
La parametrización con pool normal y un solo hilo conserva las dos cancelaciones,
la custodia del host, los recibos `unknown` originales y la comprobación sin replay.
El archivo completo pasó **28 casos en 20,83 s**, con Ruff y diff-check aprobados.
No cambian los plazos ni el código productivo de activación.

## Evidencia local y publicación

La selección focal pasó **26 pruebas, cero fallos y cero omisiones, en 43,378 s**:
los dos casos originales, las dos regresiones, WebKit/CDP/auditoría, Chrome real
con perfil nuevo propio y el bootstrap. El último ajuste posterior sólo protege
la lectura diagnóstica contra errores de E/S; los hooks validan el árbol final.

| Log local en `/private/tmp/` | SHA-256 |
| --- | --- |
| `jarvis-ci-startup-baseline-20261027.log` | `fa4e15c9d64279fb97a881c3e7f76684f7c18cc8c8baa2523448d5a142996dd4` |
| `jarvis-ci-startup-dns-red-20261027.log` | `823404be29f2d9566422656426375e2b44e60346fcef44f4e6a6238ab71af0eb` |
| `jarvis-ci-startup-dns-green-20261027.log` | `fdbb697b877cb4dc2fa740a564e3fed6c96d0c8948cd7d190d373e3fcc7b79a7` |
| `jarvis-ci-activation-single-worker-red.log` | `695f68d418a71cd3f8f26956c9b51fbffe5ddc7c5952a88d9d1599e1a1b1b0e9` |
| `jarvis-ci-activation-cleanup-single-worker-red.log` | `95f520be8eca9d394b06b03fd8e20777a9f1211268d818ec3e3eee080f810a26` |
| `jarvis-ci-activation-final-file-green.log` | `f42c8c1dd52e9891041029faf74905e22afe6c4e1be47a7360cecdacd15ab07c` |

Los cachés Swift regenerables de la reproducción fallida se retiraron después
de conservar los logs. No se borraron los respaldos ni evidencias históricas.
Esta nota es el corte anterior al commit que la contiene: los resultados finales
de hooks, publicación y CI se identifican por ese commit y su ejecución remota,
sin anticipar un SHA o resultado futuro. No cierra la fase A ni reabre los
recorridos ya cualificados del Mac y navegador.
