# Automatización — cierre del incremento elegido

**Estado: qualified_selected_increment.** Los nueve recorridos nativos de los
checkpoints 09df/d474, diagnóstico y cierre están verificados. El producto d474
permanece idéntico en 1481983; su CI exacta 40877800263 pasó completa.
selected_increment_complete=true, phase_a_complete=false y phase_b_started=false.
No quedan pendientes dentro del incremento elegido; no se declara paridad universal.

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

## AUT-N09 — bundle posterior a las correcciones, Doctor y cierre

launch-final.json identifica el artefacto 12873226089 producido por CI
40817429936 para d4746eaee62acb5cb60f300134b91a7825d4ef45. Su ZIP tiene
SHA-256 0c54fd81dfea827d4cfe9047138cd6011116e99603b0094bcf3114b3c7e04573;
el archivo del bundle, da28a4441674a85596127b7960302091c037ff062310afc357414e0f1091465f.
Binario original: 8c202876fd05e549b35df67abe95836a675d927b4b969c2ab28dc75dface1675;
binario de prueba: 479ab3798dfeef6ebe9964820adbbfb0b6bdf4ecc5801f50245415242a719bde;
wrapper: 5d5d17b5b455ff83e592b079c5c11683d53c7ae42d7df5548650dc943d31020c.
Sigue siendo una app de desarrollo con Python externo.

El control cacheado del arnés se reinició porque dos bundles sintéticos compartían
identificador. No se atribuye ese incidente del ensayo al producto ni se presenta
como una falla de la app personal. El lanzamiento final usa ruta única e
identificador local.jarvis.automation.d4746ea.final, con fuentes validadas.
La app del dueño quedó intacta y el agente no leyó ni copió credenciales.

final-bundle-oracle.json registra el flujo be92dd9d20164716a8d0f05e9f5fdf07 y
ejecución fda7ba72af3b47958b4976e8dd424c49, completed/known, respuesta real de
Codex WEBHOOK JARVIS 8124 y cero recibos de acciones. El marcador quedó visible
en la GUI. No se activó el programador ni se abrió webhook; los archivos negativos
anteriores siguieron ausentes.

El Doctor nativo f11b549e71cb4f6eae0045e792129f50 terminó y mostró
automations, schedules y state correctos. Su estado global permaneció parcial
por registros opcionales ausentes, no por corrupción de los presentes. El Doctor
no usó inferencia ni abrió cuentas.

final-bundle-close-oracle.json conserva salida desde el menú nativo, ausencia de
las apps/servicios propios, lease del perfil liberada, socket retirado y puerto
cerrado. El observador midió 108.523.520 bytes de pico bajo 256 MiB durante
217,51 segundos, terminó al salir la app y no envió señales. Conservó originales
y la instalación del dueño. Este cierre sólo se refiere al ámbito propio medido.

### Correspondencia exacta de producto d474 → 1481983

Las revisiones 855b689 y 1481983 cambian únicamente cuatro archivos de tests.
Estas identidades Git permanecen iguales entre el bundle observado d474 y la
revisión 14819831499ec4472bba506f820eb51f56b29149:

| Árbol | SHA Git |
| --- | --- |
| src | 13e01c6ee7689af65691e91c71b3e6958d95bd5f |
| native/JarvisDesktop/Sources | a086570256fa4c685dab7b1d31f76541a9d2b323 |
| script | ba30714fe48809f948b2c725b5d51876a76a095c |
| .github | 29b5332217a941d7c420c687d7f284354f13fc82 |

Los oráculos iniciales conservan su identidad 09df; el recorrido posterior d474
acredita el producto después de corregir DTO, ACK parcial y cierre. Ninguno se
convierte por sí solo en suite verde. La correspondencia de árboles permite
relacionar el último recorrido con 1481983 sin atribuirle un binario recompilado
que no se haya observado.

## CI exacta aprobada

La [ejecución 40877800263](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40877800263) para
14819831499ec4472bba506f820eb51f56b29149 terminó correctamente en ambos jobs.
Paquetes sdist/wheel y smoke de instalación también aprobaron. Todos los
supervisores confirmaron limpieza y fuentes sin cambios.

| Plataforma / etapa | Resultado | Tiempo de pruebas / supervisor | Pico asignado remoto |
| --- | --- | --- | --- |
| [Ubuntu](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40877800263/job/123461038043), Python | 3247 aprobadas, 46 omitidas | 270,80 / 272,135 s | 538.669.056 bytes |
| [macOS](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40877800263/job/123461038818), Python | 3293 aprobadas, cero omitidas | 325,05 / 326,56 s | 956.030.976 bytes |
| macOS, Swift | 487 casos, uno omitido y cero fallos | 182,299 / 256,959 s | 1.463.570.432 bytes |
| macOS, bundle | Compilación aprobada | supervisor 99,271 s | 376.807.424 bytes |

Esas cifras de suites/compilación pertenecen a runners remotos. No se ejecutaron
suites completas ni compilaciones locales. Se conservan por separado los
recorridos nativos, sus oráculos y los intentos fallidos de abajo.

### Alcance de las mediciones locales

El pico nativo final de 108.523.520 bytes y el presupuesto de 256 MiB cubren sólo
la raíz QA nueva. Un inventario final de metadatos du registró 105.984 KiB en esa
raíz, 22.020 KiB en las particiones REA y 196.204 KiB en el HOME sintético
previamente autorizado del proveedor. Este último estaba fuera de la raíz
vigilada y ya existía; no hay medida inicial que permita atribuirle crecimiento.
No se leyeron ni borraron contenidos de autenticación o historial. Los 197 GiB
libres observados son una muestra del disco, no una certificación de consumo
global o una cuota aplicada a recursos externos.

## Contratos automatizados complementarios

Estos contratos complementan los recorridos GUI de ambos checkpoints y aprobaron en la CI exacta:

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
validación aprobada. La corrección productiva quedó en d4746ea / 40817429936. Ubuntu aprobó
3247 pruebas y omitió 46 en 189,70 segundos; pico asignado 538.710.016 bytes y
limpieza confirmada. macOS compiló el bundle, pero no compiló las pruebas por
una propiedad del fixture que colisionaba con NSObject.hash. La corrección
855b689 sólo cambia pruebas.

855b689 / 40876539585 aprobó Ubuntu con 3247 pruebas, 46 omitidas y 271,49
segundos; pico 538.660.864 bytes y limpieza confirmada. Swift ejecutó 487 casos,
uno omitido y tres fallidos: dos perfiles artificiales no incluían automation.json,
por lo que Doctor devolvía correctamente parcial; otro assert histórico esperaba
ocho elementos donde ahora hay nueve. 1481983 corrige esas expectativas/perfiles
de prueba sin cambiar el producto. Su CI 40877800263 finalmente pasó completa, como registra la sección anterior.
Los fallos anteriores no se borran ni se presentan como suites verdes.

## Reconciliación de la matriz

P-AUTOMATION-EVENTS y P-SCHEDULER quedan qualified_selected_increment, con cero
pendientes dentro del incremento elegido y enlaces a este cierre y al registro.
A-46 y A-47 conservan estado de familia amplia parcial: este cierre acredita sus
recorridos elegidos, no sintaxis, plataformas o integraciones universales.

El último registro de progreso apunta a automatización y conserva MCP/extensiones
como incremento cualificado anterior. V1_PENDIENTES y README ya no ofrecen este
bloque como pendiente. Se mantienen 52 familias parciales y 28 sin implementar,
ninguna familia amplia completa, con fase A abierta y fase B sin comenzar.
Los cierres previos de recuperación, núcleo, Mac/navegador, memoria y MCP se
conservan y no vuelven a abrirse por esta reconciliación.

## Límites

Incluye calendario/heartbeat/wake del motor local abierto, hooks declarativos, flujos de hasta 16 pasos, autoridad normal del chat, webhook HMAC loopback y controles/historia nativos. No instala daemon/login item, no despierta físicamente el Mac, no publica puerto de Internet ni ejecuta módulos OpenClaw, shell o expresiones arbitrarias como hook. No acredita iPhone, push, voz continua, otro proveedor o distribución autocontenida/notarizada; corresponden a otros bloques.

## Manifiesto de archivos que sustentan el cierre

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
| launch-final.json | 1183 | 11dd2fb1606447f55436bbd5d9e8f1de7285dac95a0bc92fe99e4b2325ec5931 |
| final-bundle-oracle.json | 809 | 0670da916081a3f164fe8374d0075a4280b4a8472ad390dca9434d6f20746cad |
| final-bundle-close-oracle.json | 811 | d02ac1810e898681fe4afdd207a6b107efacd47a577f40f185512e18c14b623d |

La consolidación usa oráculos propios y documentación del repositorio. El almacenamiento del HOME sintético del proveedor se midió únicamente como metadatos agregados; no se leyeron contenidos de autenticación, historial personal ni datos privados del dueño. El registro PHASE_A_AUTOMATION_QUALIFICATION.json conserva los resultados y manifiestos completos de este alcance.
