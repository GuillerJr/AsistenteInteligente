# Automatización propia de Jarvis

Estado del incremento: **qualified_selected_increment**. Los ocho recorridos
nativos iniciales y el posterior del bundle d474, diagnóstico y cierre están
verificados. El producto de ese bundle es idéntico al de 1481983, cuya
[CI exacta 40877800263](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40877800263) aprobó Ubuntu/macOS, Swift, bundle, paquetes y smoke.
El [registro](PHASE_A_AUTOMATION_QUALIFICATION.json) y el
[cierre](PHASE_A_AUTOMATION_CLOSEOUT.md) conservan identidades, resultados y
fallos históricos. No quedan pendientes dentro de este incremento elegido.
La fase A global permanece abierta y B no ha comenzado.

Alcance elegido: app macOS Apple Silicon, servicio local y Codex con ChatGPT.
La implementación es propia. OpenClaw se estudió sin ejecutarlo mediante los
lotes REA OC-25A/B/C, conservando commit, hashes e incógnitas. Las dependencias
no seleccionadas y las plataformas aplazadas no se presentan como verificadas.

## Capacidades conectadas

| Parte | Comportamiento |
| --- | --- |
| Calendario | Una vez, intervalo, diario, cron de cinco campos y evento explícito; zona IANA, listas/rangos/pasos y nombres de meses/días. Se omiten huecos DST y se elige la primera repetición de un minuto ambiguo. Las ausencias se agrupan, sin ráfaga de trabajo atrasado. |
| Heartbeat | Intervalo con ventana activa opcional, también nocturna. Los resultados quedan en conversaciones y recibos; no se abre un canal ni se envía una notificación por defecto. |
| Wake | Identidad de evento durable, revisión exacta y motor activo. Repetir el mismo evento devuelve la misma ocurrencia; un cambio incompatible se rechaza. |
| Control | Activar, detener, suspender/reanudar el programador y cancelar una ocurrencia. Suspender cancela trabajo en vuelo y conserva incertidumbre si no se acredita el efecto. |
| Hooks | Reglas declarativas deshabilitadas al crear. Eventos manuales, terminación de conversación, terminación de tarea y webhook; destino agente/workspace fijado. El contenido externo es un dato, no instrucciones ni permiso. |
| Flujos | Hasta 16 pasos, checkpoint conocido, pausa explícita, reanudación CAS y recibo por paso. Denegación, caducidad, cancelación, reinicio o efecto incierto no repiten automáticamente el paso. |
| Webhook | Una entrada exacta por activación, sólo 127.0.0.1 y puerto efímero. HMAC SHA-256 sobre timestamp, nonce y cuerpo; plazo, límite de cuerpo, concurrencia, idempotencia durable y rechazo de firma/nonce discordantes. Secreto sólo en RAM y cierre de conexiones bajo custodia. |
| Nativo | Programador y panel de eventos con revisiones, historial paginado, estados, recibos, siguiente página y controles de revocación. Respuestas antiguas no cambian otro perfil o revisión. |
| Conservación | Scheduler SQLite v2 con migración transaccional v1; respaldo/diagnóstico/restauración reconocen wake y automation.json. Restaurar deshabilita hooks e interrumpe checkpoints activos; no abre motores, cuenta ni puerto. |

## Autoridad y ciclo de vida

El dueño revisa la configuración antes de activar cada motor. La conexión del
cliente que lo activa queda ligada al trabajo y se comprueba de nuevo antes del
efecto. Un webhook no concede autorización. Cada herramienta conserva la misma
autoridad de operación, sesión, ejecución y argumentos usada por un chat manual.
Las aprobaciones delegadas del dueño sólo se realizan en perfiles sintéticos.

Revocar un hook o cambiar su revisión invalida trabajo anterior. Una pausa durante
un efecto no se convierte en un checkpoint conocido. El siguiente paso espera la
limpieza; si el cierre no termina, se retienen propietario, descriptor y estado.
Los eventos producidos por los propios flujos no crean un bucle de hooks.

Los motores y el webhook requieren activación explícita después de arrancar.
No se instala un daemon, no se despierta físicamente el Mac y no se publica un
puerto de Internet. La suspensión del sistema se trata al volver a observar el
reloj: un único vencimiento agrupado, sin ejecución duplicada. Wake significa
despertar el motor de trabajo que ya está abierto.

## Protocolo de entrada

`POST /hooks/<hook_id>` con objeto JSON como cuerpo. Cabeceras:
`X-Jarvis-Timestamp` (segundos Unix), `X-Jarvis-Nonce` (identidad de evento) y
`X-Jarvis-Signature` (hexadecimal de HMAC SHA-256). Los bytes firmados son
`timestamp + "." + nonce + "." + cuerpo_original`. El secreto tiene 32–64 bytes
ASCII imprimibles, no se devuelve por RPC, no aparece en listados ni se conserva
en respaldo. La entrada local no sustituye un relay HTTPS autenticado remoto.

## Verificación

Las pruebas Python/Swift cubren calendario/DST, coalescencia, migración, wake,
CAS, duplicados, autorización, cancelación, checkpoint, firma y replay HTTP,
revocación, cierre incierto y restauración desarmada. Incluyen RPC/SQLite/archivos
y sockets reales con proveedores y decisiones sintéticos, separados de la
cualificación nativa y de inferencia real.

Las suites completas, compilación Swift, wheel/sdist y smoke se ejecutan en CI
remota. El registro de aceptación conserva el commit exacto y los resultados;
compilar o pasar un fixture no acredita por sí solo el bloque completo.

## Límites explícitos

Cron admite cinco campos; no ejecuta expresiones, shell, jitter ni código externo.
Los hooks son declarativos propios, no módulos ejecutables de otro producto.
Se conservan cuotas de tareas, eventos, documento, payload y transporte; alcanzar
una cuota falla de forma visible, sin borrar historia ni repetir efectos. Los
históricos se paginan para respetar el límite de IPC.

Este incremento no implementa cuentas nuevas, publicación remota, notificaciones
push, iPhone o una distribución autocontenida. Esas capacidades siguen en sus
bloques. La fase B no comienza por terminar este incremento.
