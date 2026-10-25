# Contrato de paridad de la base Jarvis / OpenClaw

Referencia fijada: OpenClaw `1e3ff306abaff29f03e7668d53b01c07f10294ac` (`2026.10.7`), 24-10-2026. Este registro define **lo que falta reconstruir**, no una variante completa disponible. El [JSON](OPENCLAW_BASELINE_PARITY.json) conserva estados, catálogos, fuentes y hashes.

## Alcance de «base completa»

La petición vigente exige primero una variante propia funcional de la base completa de OpenClaw bajo nombre Jarvis y dentro de este repositorio. Mejoras adicionales y herramientas específicas del Jarvis anterior vienen después. Un kernel pequeño, un chat y contratos MCP no bastan.

**Archivos, exec/procesos, navegador, computadora, memoria, automatización, canales, nodos y voz son familias nativas de OpenClaw: su equivalente pertenece a esta base aunque también aparezca en Jarvis histórico.** Una integración particular del producto anterior puede seguir en fase B; la capacidad de referencia no se aplaza automáticamente.

Se construye con código y recursos propios; el clon externo es de lectura y no es dependencia, vendor ni runtime. El cliente solicitado es macOS SwiftUI con ventana, Dock, menú y atajo. Las superficies de referencia necesitan equivalencia funcional en esa app; copiar vistas/recursos o exigir encontrar una pestaña no cierra el requisito.

Conectores opcionales, plataformas y experimentos siguen inventariados. «Opcional de instalación» no equivale a «eliminado», y tampoco autoriza activarlo sin elección. Un hito limitado a macOS + Codex se denomina así: no paridad universal mientras queden entradas sin ensayar o diferencias sin resolver.

## Estado inicial y evidencia

El inventario inicial partió del núcleo Python con contratos sintéticos. El avance posterior se registra abajo sin alterar ese punto de partida. Las pruebas locales no certifican paridad productiva, memoria semántica ni TCC; la inferencia autenticada tiene evidencia propia separada.

El censo fijado abarca 59,267 archivos versionados y registra **554 filas RPC core / 122 familias de handler, 67 IDs de tools core, 56 rutas UI, 177 plugins y 34 canales**. Son declaraciones, no funciones verificadas; no agotan métodos/tools dinámicos de plugins/MCP/harness.

El catálogo fija 74 plugins incluidos en core, 100 externos oficiales y 3 sólo de checkout. Conserva distribución y contratos declarativos cuando hay manifiesto. Su presencia/activación por defecto en OpenClaw no habilita nada en Jarvis.

El método es lectura estática de catálogos, manifiestos y contratos fijados. **No se ejecutó OpenClaw ni se afirma revisión semántica de todo el árbol.** E001… son IDs locales de fuente con SHA-256 y blob Git, no Evidence IDs `ev_` de REA. La cobertura REA real permanece en [REA_COVERAGE.json](REA_COVERAGE.json). Cada lote debe ampliar evidencia sin confundir censo con exhaustividad.

La conexión elegida es Codex con ChatGPT por cliente/acceso oficiales. NVIDIA está excluido. Cualquier otro proveedor de inferencia, embedding, voz o generación requiere elección; no existe fallback oculto. No se lee `auth.json`, historial ni datos privados. El dueño escribe contraseñas, concede TCC y aprueba efectos reales.

## Avance parcial posterior al inventario inicial

El [recorrido inicial](BASELINE_VERTICAL_SLICE.md) acreditó conversación autenticada con Codex, cancelación y reinicio. El [avance conectado posterior](PHASE_A_TOOLS_MEMORY_MCP.md) añadió archivos, autoridad por operación, memoria léxica y MCP stdio. La [continuación de gestión](PHASE_A_MANAGEMENT.md) añade procesos aislados, agentes, skills, programador y ramas. **Ninguna de las 80 familias completas se declara cerrada.** La app anterior instalada no se reemplazó.

| Componente | Evidencia propia y límites |
| --- | --- |
| Estado SQLite y recuperación propios | Persistencia SQLite real con datos sintéticos: sesiones/mensajes/runs/eventos, transacciones, temporales RAM, límites y recuperación interrupted/unknown sin repetir efectos. Pendiente: No memoria semántica, PostgreSQL, cifrado integral certificado, sharing/múltiples usuarios, ramas/rewind completos ni migración de datos del dueño. |
| Servicio y JSON-RPC local por socket Unix | Sockets/procesos locales reales con contenido y proveedor sintéticos; token privado, límites, idempotencia de petición, errores, cancelación y cierre. Pendiente: No identidad firmada de cliente, API completa de referencia ni acceso remoto productivo. |
| Adaptador oficial Codex app-server aislado | Acceso oficial e inferencia autenticada del recorrido inicial, reinicio y contexto propios acreditados. DynamicTools conectadas a Runtime único, protocolo/turno/IDs validados y resultados separados; pruebas subprocess propias. No se importan hilos ajenos. Pendiente: No paridad completa del harness, cuentas/uso/renovación completos ni todas las herramientas de referencia. Las herramientas builtin del proveedor permanecen desactivadas. |
| Cliente SwiftUI propio de desarrollo | Ventana/Dock/menú/atajo y conversación oficial reales del recorrido inicial. Nuevos paneles de herramientas, aprobación por operación, memoria/citas, MCP, sesiones, borradores y recibos con contratos de socket/DTO probados. Pendiente: No todas las vistas/funciones de referencia ni paquete autocontenido. Firma ad hoc y Python/Codex externos no sustituyen instalación anterior ni Developer ID/notarización. |
| Cliente stdio y registro MCP conectados a servicio/runtime/app | Procesos stdio y socket locales propios; versiones2025-11-25 y2026-07-28 explícitas, catálogo versionado, mantenimiento, no autoconexión, misma autoridad por operación consumida justo antes de escribir tools/call, revocación y efecto incierto sin replay. Pendiente: Sin servidores externos cualificados, OAuth/HTTP/SSE, apps completas, subscriptions/sampling/tasks/MRTR o JSON Schema completo. HOME privado no es sandbox OS. Recursos/prompts del transporte aún no expuestos como tools/UI de producto. |
| Herramientas de workspace y autoridad central | Filesystem real propio: listar/leer/escribir/editar/mkdir, raíz fijada, rutas privadas/enlaces rechazados, compare-and-swap de bytes, leases exactos de un uso y guard antes del efecto. Configuración privada transaccional con lock y merge. Pendiente: No exec/sandbox, parches completos, perfiles persistentes de permisos ni herramientas completas de referencia. |
| Memoria local léxica con procedencia y citas de snapshot | SQLite real propio y temporales RAM, ámbito host por agente/workspace, búsqueda local FTS5 o lexical_scan explícita, revisiones/hashes/citas y generación invalidables; guardar/olvidar pasa por autoridad común. Pendiente: No embeddings ni híbrida/semántica, indexación automática, flush completo, wiki/motores externos, cifrado integral o migración de datos personales. |

REA registra nueve lotes estáticos con 36 rutas TypeScript únicas. La cobertura global sigue parcial, conserva incógnitas semánticas y no observó ejecución de OpenClaw. Fuentes, hashes y Evidence IDs están en REA_COVERAGE.json.

La [decisión de destinos](PHASE_A_TARGETS.md) fija macOS como entrega inicial, aplaza iPhone y elige Telegram como primer canal externo. No acredita plataformas o conectores pendientes.

## Matriz funcional

Todas las filas mantienen pendiente la cualificación real propia. «Sintético parcial» sólo identifica parte del contrato probado en kernel. El JSON añade caso de fallo/revocación, dependencias y fuentes por familia.

| ID | Capacidad | Estado inicial | Caso observable propio requerido |
| --- | --- | --- | --- |
| A-01 | Servicio único, arranque y apagado | Avance contractual parcial; cierre pendiente | Dos clientes conectados a Jarvis observan la misma identidad y estado; reiniciar conserva sólo el estado durable. |
| A-02 | API tipada, descubrimiento, eventos e idempotencia | Avance contractual parcial; cierre pendiente | Consultar catálogo, ejecutar solicitud identificada y recibir eventos ordenados; repetir clave no duplica un efecto. |
| A-03 | Autenticación, ámbitos, pairing y revocación | Avance contractual parcial; cierre pendiente | El dueño empareja cliente de prueba y una operación admitida funciona bajo su identidad y ámbito. |
| A-04 | Acceso remoto seguro y descubrimiento | Pendiente | Dos equipos de prueba descubren el gateway elegido y conectan autenticados identificando el destino. |
| A-05 | Configuración, esquema y recarga transaccional | Avance contractual parcial; cierre pendiente | Cambiar preferencia mediante API propia y comprobar efecto desde otro cliente y tras reinicio. |
| A-06 | Supervisión y recuperación del servicio | Avance contractual parcial; cierre pendiente | Instalar servicio propio, provocar caída controlada y observar relanzamiento y diagnóstico efectivo. |
| A-07 | Ciclo de agente con herramientas y streaming | Avance contractual parcial; cierre pendiente | Con Codex elegido resolver consulta, ejecutar tool autorizada y continuar con su resultado en streaming. |
| A-08 | Cancelación, asentamiento y efectos inciertos | Avance contractual parcial; cierre pendiente | Cancelar streaming y herramienta muestra recibo; el siguiente run espera que se liberen recursos. |
| A-09 | Presupuestos, concurrencia, colas y steering | Avance contractual parcial; cierre pendiente | Dos sesiones avanzan separadas; una corrección dirige el run previsto y se ven límites de pasos/tiempo/bytes. |
| A-10 | Autoridad y aprobación exacta | Avance contractual parcial; cierre pendiente | El dueño autoriza argumentos de una operación/sesión/run y el recibo corresponde exactamente a ese efecto. |
| A-11 | Catálogo efectivo, perfiles y tool search | Avance contractual parcial; cierre pendiente | El modelo recibe sólo tools elegibles; buscar/describir encuentra una tool permitida que usa la misma autoridad. |
| A-12 | Contexto, instrucciones y compactación | Avance contractual parcial; cierre pendiente | Sesión larga compacta conservando instrucciones, decisiones y referencias necesarias para continuar una tarea de prueba. |
| A-13 | Recibos, procedencia y resultado verificado | Avance contractual parcial; cierre pendiente | La app distingue texto del modelo, despacho de herramienta y comprobación independiente del efecto. |
| A-14 | Sesiones persistentes, temporales y catálogo | Avance contractual parcial; cierre pendiente | Crear, renombrar, fijar, agrupar, archivar y buscar; tras reinicio persiste sólo la sesión durable. |
| A-15 | Historial, adjuntos, artefactos y transcripciones | Avance contractual parcial; cierre pendiente | Adjuntar corpus de prueba y exportar transcript con mensajes y referencias válidas bajo autoridad de la sesión. |
| A-16 | Ramas, fork, rewind y recuperación | Avance contractual parcial; cierre pendiente | Bifurcar conversación, rebobinar y recuperar run interrumpido conservando identidad y efectos. |
| A-17 | Usuarios, propiedad y colaboración | Pendiente | Dos identidades de prueba colaboran por membresía explícita; la app atribuye petición y aprobación. |
| A-18 | Decisiones, preguntas, progreso, presencia y sugerencias | Pendiente | Responder pregunta estructurada, actualizar progreso y aceptar/descartar sugerencia cambia el objeto previsto. |
| A-19 | Múltiples agentes, workspaces e instrucciones | Avance contractual parcial; cierre pendiente | Crear dos agentes con espacios separados y verificar que cada conversación usa las instrucciones/ruta elegidas. |
| A-20 | Subagentes, swarm, comunicación y objetivos | Pendiente | Padre delega dos tareas, recoge resultados correlacionados y registra objetivo con presupuesto. |
| A-21 | Codex con ChatGPT oficial y aislado | Avance contractual parcial; cierre pendiente | El dueño completa login oficial en espacio dedicado; Jarvis obtiene catálogo, chatea, usa tool y continúa tras reinicio. |
| A-22 | Contratos de proveedores y elección de modelo | Pendiente; proveedores adicionales requieren eleccion | Mostrar proveedor/modelo efectivo y capacidades; un proveedor explícitamente elegido usa sólo su transporte. |
| A-23 | Autenticación, renovación, cuentas, uso y límites | Avance contractual parcial; cierre pendiente | Completar login/logout oficial con el dueño y atribuir uso/límites al proveedor y sesión efectivos. |
| A-24 | Memoria durable, procedencia y controles | Avance contractual parcial; cierre pendiente | Guardar hecho sintético permitido y recuperarlo tras reinicio en sesión elegible con fuente/propietario. |
| A-25 | Búsqueda lexical, vectorial e híbrida | Avance contractual parcial; cierre pendiente | Indexar corpus artificial, recuperar consultas exactas/semánticas con citas y reconstruir índice con equivalencia. |
| A-26 | Recuperación activa, flush y mantenimiento | Avance contractual parcial; cierre pendiente | Recuperar recuerdos pertinentes antes de responder y ensayar mantenimiento con trazabilidad de cambios. |
| A-27 | Motores opcionales, wiki y migración | Pendiente; opcional o experimental de referencia | Con capa elegida importar corpus artificial, consultar fuentes, exportar y restaurar sin pérdida/duplicado. |
| A-28 | Almacenamiento, transacciones, migración y privacidad | Avance contractual parcial; cierre pendiente | En perfil aislado escribir estado durable, migrar y recuperar tras corte verificando identidad y contenido. |
| A-29 | Listar, leer, escribir, editar y aplicar parches | Avance contractual parcial; cierre pendiente | En workspace temporal crear/editar texto, aplicar parche y verificar bytes y diff independientes. |
| A-30 | Exec, procesos, stdin, terminal y cancelación | Avance contractual parcial; cierre pendiente | Ejecutar programa benigno en sandbox, enviar stdin, observar exit/salida y cancelar desde app. |
| A-31 | Sandbox, ubicación de ejecución y privilegios | Avance contractual parcial; cierre pendiente | Mostrar si efecto ocurre en host/sandbox/nodo; comando autorizado sólo modifica carpeta de prueba. |
| A-32 | Modo de código y llamadas compuestas | Pendiente; opcional o experimental de referencia | Si se habilita experimento, programa corto compone tools permitidas con límites y resultados correlacionados. |
| A-33 | Búsqueda web, extracción y referencias | Pendiente | Con proveedor elegido buscar/extraer página pública de prueba y devolver referencias que abren el recurso consultado. |
| A-34 | Navegador administrado, perfiles y pestañas | Pendiente | Abrir perfil aislado, navegar página local, localizar/rellenar/pulsar y verificar estado resultante. |
| A-35 | Navegador existente y relay | Pendiente | El dueño adjunta pestaña de prueba explícita; Jarvis actúa sólo allí y muestra destino. |
| A-36 | Observación y acciones reales del escritorio | Pendiente | En documento vacío observar ventana/controles, autorizar efecto y verificar texto sin confundir observación con guardar capturas. |
| A-37 | Pantallas, paneles, ventanas y UI móvil | Pendiente | Seleccionar superficie exacta de prueba y manipularla; en móvil consentido observar y actuar en app prevista. |
| A-38 | Preguntas y secretos invisibles al modelo | Avance contractual parcial; cierre pendiente | Pedir decisión y credencial de prueba por interfaz segura; adaptador usa secreto sin transcript ni logs. |
| A-39 | Clientes MCP, transports, catálogo y lifecycle | Avance contractual parcial; cierre pendiente | Servidor MCP real de prueba descubre tools/recursos, ejecuta operación autorizada y cierra conexiones. |
| A-40 | Autenticación MCP, recursos y apps | Avance contractual parcial; cierre pendiente | OAuth de prueba autoriza cuenta concreta; listar/leer recurso y llamar app/tool conservan identidad. |
| A-41 | Servidor/relay y eventos MCP | Pendiente | Cliente externo de prueba ve sólo catálogo admitido; suscripción entrega evento correlacionado. |
| A-42 | Registro, contratos, hooks y lifecycle | Pendiente | Cargar extensión propia declarada, invocar tool y deshabilitar/recargar liberando recursos. |
| A-43 | Catálogo, instalación, actualización y eliminación | Pendiente | Instalar paquete propio de prueba desde fuente explícita, inspeccionar, actualizar y desinstalar con UI/runtime coherentes. |
| A-44 | Skills, biblioteca, búsqueda y entrega | Avance contractual parcial; cierre pendiente | Crear skill propia con recursos de prueba y activarla sólo para agente previsto. |
| A-45 | Workshop, propuestas, revisión y reversión | Avance contractual parcial; cierre pendiente | Proponer cambio, revisar diff, aprobar con dueño, aplicar y deshacer conservando versiones. |
| A-46 | Cron, automatizaciones, heartbeat y wake | Avance contractual parcial; cierre pendiente | Programar trabajo de prueba con zona horaria, observar ejecución, reiniciar y comprobar persistencia; pausar/borrar funciona. |
| A-47 | Hooks, webhooks y flujos reanudables | Pendiente | Evento local y webhook autenticado disparan flujo correcto; pausa/aprobación/reanudación conserva correlación. |
| A-48 | Entrada/salida, routing y entrega multicanal | Pendiente | Cuenta elegida recibe mensaje/adjunto y responde una vez al agente/sesión/hilo/destino exactos con recibo. |
| A-49 | DM, grupos, menciones, pairing y acciones | Pendiente | Emparejar identidad, probar activación por mención y acciones que el canal anuncia como admitidas. |
| A-50 | A2A, canales entre agentes y conversaciones externas | Pendiente | Dos instancias propias intercambian mensaje autenticado y respuesta correlacionada con identidades y límites. |
| A-51 | Registro, pairing, capacidades e invocación | Pendiente | El dueño empareja nodo de prueba; describir muestra comandos reales e invocar entrega progreso/resultado atribuibles. |
| A-52 | Cámara, pantalla, ubicación, avisos y archivos | Pendiente | En dispositivo consentido verificar captura/observación, ubicación, notificación y transferencia con contenido/destino correctos. |
| A-53 | Sesiones alojadas, pendientes y presencia | Pendiente | Nodo anuncia sesión propia, reconecta y confirma trabajo pendiente una sola vez. |
| A-54 | Micrófono, PTT y activación por voz | Pendiente | Con permiso del dueño iniciar PTT y wake habilitado explícitamente y enviar sólo transcripción del turno de prueba. |
| A-55 | STT, TTS, Talk y voz en tiempo real | Pendiente | Con proveedores elegidos transcribir, responder por voz, interrumpir salida y continuar sin mezclar turnos. |
| A-56 | Imágenes, PDF, documentos, video y adjuntos | Pendiente | Analizar corpus artificial citando archivo y página/cuadro para comprobar contenido observado. |
| A-57 | Generación de imagen, música y video | Pendiente; opcional o experimental de referencia | Servicio elegido genera medio de prueba con progreso y artefacto que se abre/reproduce realmente. |
| A-58 | Reproducción y acceso a recursos de medios | Pendiente | Reproducir audio/video local, pausar/buscar y cambiar salida admitida preservando identidad del recurso. |
| A-59 | App macOS nativa bajo marca Jarvis | Avance contractual parcial; cierre pendiente | Abrir app SwiftUI propia con ventana/Dock/menú/atajo; conversar en español con streaming/cancelación/recibos reales. |
| A-60 | Gestión de sesiones, paneles y ajustes | Avance contractual parcial; cierre pendiente | Desde app gestionar agentes/modelos/memoria/MCP/automatización/dispositivos; cada control cambia runtime y persiste cuando corresponde. |
| A-61 | Widgets, canvas, dashboards y temas | Pendiente | Presentar widget/dashboard/tema propios y verificar interacción autorizada y datos de prueba actualizados. |
| A-62 | Portales, apps MCP y vistas de plugins | Pendiente | Abrir/cerrar portal/app de prueba manteniendo ámbito, destino y estado en app nativa. |
| A-63 | Proyectos, repositorios y worktrees | Pendiente | Registrar repo temporal, crear worktree, asociar sesión, inspeccionar diff y restaurar archivo/snapshot. |
| A-64 | Identidad GitHub, revisión y publicación | Pendiente | Con autorización y repo de prueba mostrar identidad/checks/diff y preparar publicación vinculada a sesión. |
| A-65 | Workboard, tareas y coordinación durables | Pendiente; opcional o experimental de referencia | Con extensión propia habilitada crear tablero/tarea, reclamar, avanzar y completar con evidencia; reinicio conserva datos. |
| A-66 | Reuniones, telefonía, correo y bitácora | Pendiente; opcional o experimental de referencia | Elegir integración y ensayarla con recursos de prueba observando evento/artefacto/resultado autorizados. |
| A-67 | Workers, entornos, cloud sessions y escritorio remoto | Pendiente; opcional o experimental de referencia | En entorno elegido provisionar prueba, despachar sesión, recuperar resultado y liberar lease/recursos. |
| A-68 | CLI, TUI, ACP y clientes de automatización | Pendiente | Controlar sesión desde CLI/TUI y cliente ACP de prueba con los mismos eventos y autoridad que app. |
| A-69 | Onboarding, asistente de sistema y doctor | Pendiente | Instalación limpia detecta requisitos y guía en español hasta primera conversación real con acceso del dueño. |
| A-70 | Logs, auditoría, actividad, métricas y límites | Avance contractual parcial; cierre pendiente | Reproducir error y reconstruir solicitud/autorización/despacho/resultado por IDs en auditoría saneada. |
| A-71 | Backup, restauración, actualización y rollback | Pendiente | Respaldar perfil artificial, actualizar paquete propio y restaurar sesiones/config/artefactos verificando hashes. |
| A-72 | Distribución, instalación y firma propias | Avance contractual parcial; cierre pendiente | Construir app desde checkout limpio, instalar en destino de prueba y verificar firma/identidad/chat con backend propio. |
| A-73 | Defensas de red, contenido, archivos y abuso | Avance contractual parcial; cierre pendiente | Corpus adversarial ensaya prompt externo, SSRF, traversal, spam y replay sin bloquear operación legítima. |
| A-74 | Clientes y nodos iOS/Android | Pendiente; port de referencia pendiente de decision | En cada plataforma adoptada instalar cliente propio, emparejar, conversar y comprobar sólo comandos consentidos. |
| A-75 | Linux, Windows, headless y hospedaje | Pendiente; port de referencia pendiente de decision | En cada sistema adoptado arrancar gateway/cliente propio y comprobar chat/servicio/sandbox/comandos compatibles. |
| A-76 | Claws, catálogo de agentes, authoring y add | Pendiente; opcional o experimental de referencia | Si se adopta experimento, revisar agente propio de prueba y sus requisitos antes de añadirlo en espacio independiente. |
| A-77 | Advisor, autoreview, diffs y asistencia de decisión | Pendiente; opcional o experimental de referencia | Si se elige función propia, revisar diff y mostrar juicio/sugerencia con procedencia sin cambiar autoridad. |
| A-78 | Importadores y migraciones externas | Pendiente; opcional o experimental de referencia | Si se adopta importador, leer sólo fixture exportado, mostrar plan/diff y aplicar/revertir sin duplicado/secreto. |
| A-79 | Notificaciones, push, suspensión y reanudación | Pendiente | Con permiso elegido notificar fin de prueba, suspender/reanudar y recuperar estado sin repetir aviso. |
| A-80 | Conectores individuales del catálogo | Pendiente | Por cada entrada adoptada completar caso real de su función con cuenta/recurso de prueba y versión/evidencia. |

## Catálogos que no se pierden al simplificar

El JSON conserva individualmente las 554 filas RPC, los IDs core de herramientas, las 56 rutas de interfaz, 177 plugins con distribución/manifest/contratos y 34 canales. El descenso a cada handler/factory y variante está pendiente: un nombre no demuestra parámetros, permiso ni efecto.

Los canales externos necesitan fuente adicional fijada. Se distingue Codex elegido, NVIDIA excluido y proveedores no elegidos. Móviles, Linux/Windows/headless, servicios remotos, plugins opcionales y experimentos siguen visibles aunque falte hardware, cuenta o decisión.

Una diferencia requiere motivo, alcance y decisión explícita. Excluir no vuelve implementada una fila. Un botón deshabilitado que explica dependencia es honesto, pero no equivale a implementar el conector.

## Cierre de fase

- Revisar por partes cada familia, handler, factory y entrada de efecto con fuentes fijadas, hashes, evidencia REA pertinente e incógnitas; reconciliar nuevas capacidades descubiertas.
- Resolver A-01..A-80 y cada entrada aplicable de catálogo con implementación propia y recorrido real observable; ni inventario ni dobles certifican integración.
- Conectar proveedor elegido, herramientas nativas y transportes productivos a autoridad única; comprobar denegación/revocación inmediatamente antes del efecto por todas las entradas.
- Cualificar app nativa, CLI/API, servicio, estado/memoria, plugins/MCP, automatización, canales/nodos y medios con recursos aislados.
- Ensayar cancelación, caída/reinicio, recuperación, aislamiento, temporalidad, límites y efectos inciertos sin fallback oculto.
- Construcción y paquete propios reproducibles; firma/instalación verificadas y conversación E2E con backend propio.
- Capacidad opcional, experimento, cuenta no elegida o plataforma sin ensayo sigue pendiente o lleva diferencia explícita acordada; excluir no equivale a implementar.
- No afirmar paridad universal con entradas pendientes/excluidas. Sólo después de cerrar el alcance completo acordado integrar mejoras adicionales y herramientas históricas específicas de Jarvis.

Por capacidad se registra recorrido **entrada → identidad/ámbito → autorización → efecto → recibo/evento → persistencia → cierre**, separando fuente estática, REA, pruebas sintéticas e integración real. Debe cubrir denegación/revocación, cancelación, cambio de destino, caída/reinicio y efecto incierto cuando apliquen.

Cuenta/hardware o permisos que debe preparar el dueño son bloqueadores explícitos con paso mínimo requerido. El agente no concede esos permisos ni introduce credenciales. Mientras falte la prueba, el estado sigue pendiente.

## Infraestructura y material que no acreditan funciones

| Ámbito | Tratamiento |
| --- | --- |
| test, qa, fixtures y benchmarks | Infraestructura de verificación de referencia; no herramientas disponibles. Crear pruebas propias y caso productivo observable, sin copiar fixtures. |
| scripts, config, .github, deploy, Dockerfile y empaquetado | Construcción/CI/hospedaje; mecanismos propios y decisiones explícitas de plataformas, sin copiar pipeline ni ejecutar clon. |
| docs generados, SDK/codegen, schemas y assets | Fuentes de catálogo/contrato, no prueba runtime. No importar recursos gráficos ni implementación generada; generación propia verificable. |
| contribuciones, ejemplos, skills y extensiones sólo fuente | No inferir distribución/soporte de una carpeta. Manifiesto, dependencias, selección y prueba por entrada pendientes. |

Los inventarios históricos de Jarvis permanecen congelados. Este contrato añade la base OpenClaw: no reemplaza sus capacidades, no reactiva código retirado y no convierte la app instalada en la nueva variante.

## Próxima partición

Estudiar una familia con dependencias/entradas de efecto, fijar fuentes/hashes y evidencia REA pertinente, escribir contrato propio y ensayar casos en datos aislados. Reconciliar hallazgos con esta matriz. Un estado «complete-within-view» no cierra el repositorio entero.
