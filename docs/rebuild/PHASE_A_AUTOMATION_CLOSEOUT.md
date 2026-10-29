# Automatización — alcance nativo elegido verificado, CI final pendiente

**Estado: awaiting_final_ci.** Los ocho recorridos nativos finitos y el cierre
presupuestado del checkpoint 09df están verificados. selected_increment_complete=false:
faltan CI y recorrido mínimo del bundle final después de corregir DTO de
recuperación, ACK parcial y retry de cierre de flujos. phase_a_complete=false;
phase_b_started=false. Este cierre en curso no acredita paridad universal.

## Identidad y alcance

- Destino: macOS Apple Silicon, app SwiftUI propia y servicio local.
- Inferencia observada: cliente oficial Codex con ChatGPT.
- Referencia OpenClaw leída sin ejecutarla: commit 1e3ff306abaff29f03e7668d53b01c07f10294ac; REA OC-25A/B/C. Bundle de evidencia estática SHA-256 3968930851bc99d6487ceae7bf28ec3daedcb4fda8b42c685533db2033963ac3.
- Producto propio: no importa módulos, runtime o recursos OpenClaw. Los lotes estáticos seleccionados no son revisión exhaustiva ni equivalencia global.
- Aprobaciones delegadas únicamente en el perfil sintético. La instalación personal del dueño sigue independiente.
- Sin suites completas ni compilaciones locales; el bundle procede de CI.

El registro launch-09df678.json conserva:

| Campo | Identidad |
| --- | --- |
| Commit del bundle | 09df67826ff522bcbfa9dfaeeadafc5f399d504a |
| CI que produjo el artefacto | 40769051425 |
| Árbol de fuentes declarado | fe7e5553f87d2f099551faa093ca21dc97855e95 |
| ZIP del artefacto SHA-256 | 11a3054566a0bf3b15c63af444f302f4f255ecb3b4c2aebaeb0f0447e89ae4be |
| Archivo del bundle SHA-256 | c8b09a6ab7dcdd8a66440455fdb0f00ede2e06e94d9de5f88f844ff69e9f6216 |
| Binario original SHA-256 | 009aee1d305f8fb3359c9961f85668e83daf3ee3e5a2bd858f9c8a660edfbc75 |
| Binario de prueba SHA-256 | ee6f8c8b4c51b200e7a2037c200a8a3b6a65b8c8217142525709db54b5630389 |
| Wrapper SHA-256 | 1453dc0725c31bd085a5276f66e67bb6b9f41d7a1ebafd8478e264c7cbab8a06 |
| Presupuesto/plazo declarados | 256 MiB / 3600 segundos |

Es un bundle de desarrollo con Python externo. Compilar ese bundle no acredita la suite Swift del intento: el job anterior fue cancelado. El registro declara reutilización del HOME sintético del proveedor previamente autorizado; el agente no copió ni leyó credenciales.

El checkpoint histórico 07969218cff872e78f02df819e906ba08cc4ce17 se conserva separado de la revisión final pendiente. La comparación Git 09df678..0796921 sólo cambia tests/test_service_scheduler_events.py; el código productivo del recorrido es el mismo. Esa correspondencia no sustituye la CI exacta final.

## Recorridos ya sustentados por oráculos independientes

### AUT-N01 — primer paso y checkpoint

step-a-oracle.json registra el flujo db2affc51b404cac8271a9fc7b507fce, revisión 4, paused/known, cursor 1. El primer recibo completed/known pertenece a la ejecución 233c252a1d854e45a81fed718e5df1cd. El archivo tiene 28 bytes y SHA-256 58434085415c7859f9acf00fd46e2815ec68d46d604db118311c4628b01095df. El segundo archivo todavía no existía. Esa ausencia comprueba que el checkpoint impidió el segundo efecto; el texto del modelo no se usa como prueba de escritura.

### AUT-N02 — reanudación y segundo permiso de herramienta

flow-oracle.json registra el mismo flujo en revisión 8, completed/known y cursor 2, con dos recibos separados. El segundo run es cbab1af041be4a988ebafddd6f09a9da. El registro identifica Codex oficial y aprobaciones delegadas en datos sintéticos.

| Archivo | Bytes | SHA-256 |
| --- | --- | --- |
| automatizacion-a.txt | 28 | 58434085415c7859f9acf00fd46e2815ec68d46d604db118311c4628b01095df |
| automatizacion-b.txt | 28 | b6d2ece50bb9fe89071a0ee10908513770dd193098a497fdea6829a4b34ec9ab |

La configuración del flujo no sustituyó la aprobación individual de cada workspace_write. Evento, flujo, paso, run y archivo permanecen correlacionados.

### AUT-N03 — cron, heartbeat y detención

timers-and-webhook-oracle.json conserva tres ocurrencias completed/known, con respuesta de modelo y sin recibos de acciones:

- Cron en 1793222220: f77463f22a114a71899e9a704f986366.
- Cron en 1793222280: aed8a615ba3c41998cc18a3ab4b3e2cb.
- Heartbeat en 1793222304.6228569: 8f1aed3c2fd242a1a95c2499a4dc40ea.

El corte posterior a detener el programador conserva un total de tres ocurrencias. No demuestra todos los calendarios ni duración indefinida; las variantes DST/coalescencia son contratos automatizados separados.

### AUT-N04 — webhook, idempotencia y cierre

webhook-oracle.json documenta un cliente independiente contra el hook 5267a95792d94e2aa01be982488a3ae9 en loopback:

| Petición | Resultado |
| --- | --- |
| Firma incorrecta | HTTP 401 |
| Firma y fecha válidas | HTTP 202; flujo 8801f1fb6ac8450f8b3347606faed6b6 |
| Repetición exacta | HTTP 202; devuelve el mismo flujo |
| Mismo evento con contenido discordante | HTTP 409 |
| Fecha caducada | HTTP 401 |

El payload tiene 36 bytes y SHA-256 2d47021d7cc9d737498297b927627f0afc2cb9cc1762550f2db31a7230a386b1. El secreto no aparece en este borrador.

El oráculo combinado conserva un único recibo conocido para el flujo, run 75ab2814ba364aa8bafecd98df492dd6, respuesta WEBHOOK JARVIS 8124, puerto cerrado y secreto ausente de automation.json. Es una entrada 127.0.0.1; no acredita Internet, TLS público ni un relay remoto.

## AUT-N05 — revocación durante una aprobación pendiente

negative-oracle.json conserva el flujo ece6d240b1534e758ea4aeb6ad61610e,
interrupted/unknown, cursor 0 y ejecución 5c95a85de72c45818572c5cdc08d45a6.
Su recibo conserva automation_hook_changed. La revocación nativa estaba disponible
mientras esperaba la herramienta; la tarjeta pendiente desapareció. Los archivos
no-debe-existir.txt y no-segundo.txt permanecieron ausentes. No se transforma una
cancelación de efecto incierto en éxito ni en checkpoint reanudable.

## AUT-N06 — wake, suspensión y reanudación sin replay

La tarea por evento bac3911f8bd14d7292ffecb936a18b3c admitió la ocurrencia
d3daf50932f1462db0dff706c30ae451, revisión 2, por wake explícito. Se suspendió
desde la app antes de aprobar workspace_write. El recibo quedó cancelled/unknown
con scheduler_dispatch_cancelled; no-suspension.txt siguió ausente y la tarjeta
pendiente se retiró. El conteo durante la suspensión era cuatro.

resume-no-replay-oracle.json conserva cuatro ocurrencias después de reanudar y
archivo ausente: la misma ocurrencia no volvió a despacharse. Las reglas de
coalescencia al volver de una ausencia prolongada tienen pruebas de reloj y
contrato separadas; no se atribuye ese escenario adicional al recorrido GUI.

## AUT-N07 — cancelar desde el historial nativo

cancel-oracle.json conserva la segunda ocurrencia por evento
77b51623b25a4a41a7e7e5d99a4f7f45, de la misma tarea y revisión. Mientras esperaba
workspace_write, el control Cancelar de Ver ejecuciones produjo cancelled/unknown,
retiró la tarjeta pendiente y actualizó el historial sin pulsar Actualizar.
El archivo permaneció ausente y el total de ocurrencias quedó en cinco. Esta ruta
acredita cancelación del programador, además de los controles de flujos.

## AUT-N08 — reinicio desarmado y cierre

before-restart-oracle.json y restart-oracle.json conservan los mismos conteos:

| Estado propio | Antes y después |
| --- | --- |
| Sesiones / runs | 10 / 9 |
| Mensajes / eventos | 15 / 254 |
| Reglas / ocurrencias / wakes | 3 / 5 / 2 |
| Hooks / flujos después de reabrir | 2 / 3 |

automation.json conserva SHA-256
fa80efcbbad9cea525c8bd543a9343ae6a8468d52da52317d22d2dfe0700a81f.
La GUI mostró el programador y el motor de flujos detenidos, webhook cerrado y
campo de secreto vacío. No apareció un nuevo run; los recibos desconocidos se
conservaron sin Reanudar y los archivos negativos siguieron ausentes.

first-close-oracle.json conserva la salida de la primera app y puerto cerrado.
Su patrón inicial -m no basta para acreditar el servicio, que arranca con Python
-c. El final-close-oracle.json corregido comprueba Python anclado y rutas exactas
de fuentes/perfil: no encuentra el servicio propio; además adquiere y libera la
lease del perfil y comprueba el socket retirado. Conserva salida de la segunda
app, puerto cerrado, originales sintéticos y app personal intactos. Son controles
del ámbito propio, no un censo universal de procesos del Mac.

Los dos observadores registraron picos de 58.634.240 y 58.650.624 bytes bajo
256 MiB y plazos de 3600 segundos. Duraron 870,092 y 38,704 segundos, terminaron
por salida de su app y no enviaron señales de terminación. Sólo midieron la raíz
propia; no leyeron contenidos de credenciales. Esa observación no atribuye el
consumo global del sistema al producto ni a otras aplicaciones.

## Pendientes finales de aceptación del incremento

Falta la CI completa del commit final exacto. La ejecución 40770485931 de
0796921 aprobó Ubuntu: 3245 pruebas, 46 omitidas, 191,73 segundos, limpieza
confirmada y pico asignado de 538.660.864 bytes. macOS compiló el bundle pero
falló 15 casos Swift de recuperación/mantenimiento con invalidResponse.
La corrección de esos DTO está en curso. Se investigan además ACK parcial y retry
de cierre de flujos; sus arreglos productivos requieren regresiones y un recorrido
mínimo con el bundle final de CI. No se declara esta CI verde ni se atribuyen a
ese nuevo bundle los oráculos anteriores.

La relación de fuentes 09df→079 sólo cubre un cambio de test. No se extiende
automáticamente a un arreglo productivo posterior: conservar la identidad del
bundle nativo observado y registrar el ámbito de la corrección, su commit y su
CI exacta. El resultado final pendiente no invalida los oráculos del recorrido
09df, pero éstos tampoco sustituyen la validación final del paquete.

## Contratos automatizados complementarios

Estos casos existen en fuentes y complementan los ocho recorridos GUI; el resultado global final depende de CI:

- Zona IANA, huecos/repeticiones DST, cron de cinco campos, coalescencia y heartbeat diurno/nocturno.
- Wake durable, CAS, duplicados, resultado incierto y convivencia con slots de calendario.
- Suspensión con lease retenida, reanudación bloqueada si hay limpieza pendiente, callback que suprime cancelación sin falso éxito.
- Autoridad central: aprobación antigua invalidada al suspender/revocar; denegación no ejecuta el paso siguiente.
- ACK de arranque perdido: custodiar y cancelar el runtime que sí empezó; no esperar uno inexistente ni repetir la ocurrencia.
- Payload/cuotas/paginación/transporte; HMAC/fecha/nonce/replay/desconexión y propietarios retenidos si falla el cierre.
- SQLite v1→v2, respaldo/diagnóstico/restauración desarmada de reglas, wake y automation.json.
- UI: controles descendentes disponibles durante waiting_approval, admisión bloqueada mientras otra mutación, y lecturas de otra generación descartadas.

## Conservar intentos históricos

ce9582a / 40767488514 tuvo tres fallos Ubuntu: fixture TCP que no admitía RST, expectativa histórica sin automations e invocación interna sin identidad sintética. Conserva 3229 aprobadas, 3 fallidas y 46 omitidas, con limpieza confirmada. El bundle macOS compiló; su suite Swift no se presenta como verde.

El intento 29c83574fc515c29c810e66b2150eaed5d5fb0b4 / 40768356264 fue
cancelado y sustituido; se conservan sus jobs 123098816855 y 123098817292.
El intento 09df678 / 40769051425 tuvo Ubuntu con 3244 aprobadas, 1 fallida,
46 omitidas y 282,11 segundos: el fixture de pérdida de ACK usaba una regla
once incompatible con el momento de la inyección. Se corrigió en 0796921 usando
un evento explícito. Su job Swift fue cancelado; el artefacto compilado conserva
identidad propia y no se convierte en suite verde. El primer ce9582a también
terminó con Swift cancelado. Ningún intento cancelado/sustituido demuestra
validación aprobada. El registro deja un next_attempt pendiente para la revisión
final, sin inventar SHA, run o resultado.

## Integración de la matriz después de completar aceptación

1. Sólo después de la CI y recorrido mínimo del bundle final actualizar P-AUTOMATION-EVENTS a qualified_selected_increment, enlazando registro, cierre y oráculos saneados.
2. Reconciliar P-SCHEDULER: retirar los anuncios de futura cualificación de este mismo incremento una vez acreditado. Mantener límites de sintaxis, daemon OS, relay remoto y notificaciones.
3. Añadir evidencia a A-46 y A-47: programación/persistencia/pausa; evento local y webhook autenticado con checkpoint y negativos correlacionados. Conservar el vocabulario de la matriz y describir exactamente la aceptación satisfecha. Cerrar el incremento finito no demuestra equivalencia universal de esas familias.
4. Reconciliar V1_PENDIENTES y README para no volver a ofrecer este bloque terminado como pendiente. No reabrir incrementos de recuperación, núcleo, Mac/navegador, memoria y MCP ya cualificados.
5. Mantener phase_a_complete=false y phase_b_started=false; no añadir plataformas, proveedores, canales ni opcionales sin su elección.

## Límites

Incluye calendario/heartbeat/wake del motor local abierto, hooks declarativos, flujos de hasta 16 pasos, autoridad normal del chat, webhook HMAC loopback y controles/historia nativos. No instala daemon/login item, no despierta físicamente el Mac, no publica puerto de Internet ni ejecuta módulos OpenClaw, shell o expresiones arbitrarias como hook. No acredita iPhone, push, voz continua, otro proveedor o distribución autocontenida/notarizada; corresponden a otros bloques.

## Manifiesto de archivos que sustentan este borrador

Oráculos originales en /private/tmp/jarvis-automation-live-20261028. El registro JSON conserva también sus datos seleccionados:

| Archivo | Bytes | SHA-256 |
| --- | --- | --- |
| step-a-oracle.json | 559 | 08fa74058de5ab9578a45e973c6d7b69fcebf98412d9faded5adf90c99ebb1b0 |
| flow-oracle.json | 1042 | 0f3fb1669ffb5372488e6a816427844e387509f44b101145ebd42cab9f763599 |
| webhook-oracle.json | 909 | de4f6e002e873ff25bfaccc17755bfaffe1bf470475253f8dc33adc5ba7ee56f |
| timers-and-webhook-oracle.json | 2015 | b1d438e7dbe394dd44f9b12c6dd0d4000c5249f9cc0e9185b7eabfab68e18c5f |
| launch-09df678.json | 1016 | 32aeca67e05b98d0878ab834849bbee7d4b7ec81d02be43f4b9620609acf9634 |
| negative-oracle.json | 1608 | 9a8944fb2c13367e923d4ae906fadb9eb600bec76bb400bc3d2972c291a0fe18 |
| resume-no-replay-oracle.json | 149 | 84cb03e4d09b5fe5247276c46afa0766a3ca7febdb5a3600603ad49611fc57ee |
| cancel-oracle.json | 988 | 5652a1fe11887b09bb0499724b89070f09f3322e8b239fb23a88d0902adb6f64 |
| before-restart-oracle.json | 424 | e320df1168a9ab32fb992f6f59ca00e369309519663863e3290bf059a2a46eab |
| restart-oracle.json | 787 | 84d8614af55600bd2e9e33abbb567eb751378f8102bec428ad80f40a60f48024 |
| first-close-oracle.json | 179 | 10ac51cab91817e188e00915350711baf4d138f8aadce812aec415ca98e76662 |
| final-close-oracle.json | 871 | 55a36fca819438cf2d989272dfbe6a63fcc6fa79571b9862065085193818db88 |
| watch-first-close.json | 439 | 3799566be379f8bbfde6a9a951aca456fed78c2de54975c730d4c623ca454ee1 |
| watch-final-close.json | 438 | 67abe105f15686eaea7b522677964c0750eda0aa09ca6bc56ee16fa43c4e2aa9 |

Sólo se leyeron oráculos propios y documentación del repositorio. No se consultaron directorios del proveedor, autenticación ni datos del dueño. El registro PHASE_A_AUTOMATION_QUALIFICATION.json conserva los resultados y manifiestos completos de este alcance.
