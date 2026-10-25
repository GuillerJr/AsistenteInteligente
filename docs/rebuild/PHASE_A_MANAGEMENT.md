# Fase A: procesos, agentes, skills y programador

Continuación propia del 24-10-2026. OpenClaw sigue siendo una referencia estática
de lectura; no se ejecuta, modifica, importa ni incorpora como runtime. La fase B
no se inicia. Ver [destinos y conexiones elegidos](PHASE_A_TARGETS.md).

## REA por partes

OC-07 examina supervisión de procesos, cancelación y plazo de limpieza; OC-08,
ámbito de agentes e importación/selección de revisiones de skills; OC-09, cálculo
de horarios, política de tools programadas y recuperación de arranque. Cada lote
contiene cuatro rutas TypeScript, con bytes contrastados con los blobs Git de la
revisión fijada. Los manifiestos y resúmenes conservan hashes y Evidence IDs.

Observaciones estáticas acotadas: la limpieza no renueva su plazo por cada señal;
la biblioteca fija selecciones a revisiones y preserva su orden; la recuperación
de una ejecución one-shot incierta no vuelve a ejecutarla automáticamente. Las
dependencias y variantes externas a las particiones permanecen sin revisar.
Son nueve lotes y 36 rutas TypeScript únicas; REA conserva 687, 612 y 1057 incógnitas
semánticas en estos tres lotes. El censo y el AST no prueban ejecución.

La evidencia completa se exportó a
`/private/tmp/jarvis-openclaw-rea-phase-a-parts07-09-20261024.json` (16.074.197 bytes).

## Procesos y aislamiento

`ProcessTools` ofrece `process_run`, `process_poll`, `process_send` y `process_stop`.
Las mutaciones usan la autoridad central de sesión/run/operación/argumentos.
El guard se revalida justo antes de crear el proceso, escribir stdin o detenerlo.
Los IDs pertenecen a una conversación; conocer el ID de otra no da acceso.

En macOS se usa Seatbelt mediante el helper del sistema: acceso a la carpeta
elegida y runtimes de lectura, red denegada y fork/posix_spawn denegados dentro del
proceso. Las pruebas reales rechazan archivos externos, rutas privadas y alias de
volumen que permitirían alcanzar datos ajenos al workspace. La ruta y el inode se
revalidan; un escaneo acotado rechaza enlaces y entradas inseguras antes de lanzar.
Esto no certifica defensa contra otro proceso malicioso del mismo usuario que
modifique simultáneamente el host.

Linux requiere bubblewrap confiable y namespaces utilizables. Si no existen,
informa indisponibilidad y no ejecuta fuera de sandbox. No acredita una app Linux.
Hay plazos, cupos, cancelación, stdin/EOF, salida paginada, hash y truncado. Se
retienen hasta 64 resultados de procesos en RAM; los recibos del run permanecen
en el almacén de conversación. Cerrar/borrar una sesión espera la limpieza de sus
procesos, y cambiar de carpeta se rechaza mientras haya uno vivo. Exit 0 acredita
estado del proceso, nunca el efecto real de un comando.

## Agentes, skills y ramas

Los perfiles explícitos guardan nombre e instrucciones con revisión; `jarvis` es
el predeterminado. Cada conversación fija su agente al crearla. Todos comparten
la carpeta elegida por el dueño y la memoria se separa por agente/carpeta. Cambiar
un perfil o sus skills abre un hilo propio nuevo con contexto completado acotado;
no enumera hilos del proveedor ni reejecuta acciones pasadas.

La biblioteca importa sólo `SKILL.md` desde una carpeta física elegida y guarda
un snapshot con hash, procedencia y revisión. Se importa desactivado. Habilitar,
editar o retirar exige la versión y el hash revisados; un origen que cambió se
rechaza. El contexto habilitado se acota a 16.000 bytes, con omisiones explícitas.
Los recursos declarados son metadatos: no se abren ni ejecutan por importarlos.
El frontmatter admitido es reducido; no equivale a todo YAML ni a la biblioteca
remota o al workshop completos de referencia.

Una skill no autoriza herramientas. Sus instrucciones llegan como datos externos
al contexto; incluso contenido hostil debe atravesar la misma aprobación humana.
No se elimina un perfil que todavía tenga conversaciones, skills, memoria o tareas.

El esquema SQLite propio migra de v1 a v2 atómicamente. `sessions.fork` copia texto
completado hasta el punto elegido, conserva agente y temporalidad y registra el
origen. No copia runs, permisos, efectos ni identidad de hilo remoto. Las ramas
temporales permanecen en RAM. La operación rechaza contextos que exceden su límite,
sin crear una conversación parcial. No sustituye un rewind destructivo completo.

## Programador

Las tareas explícitas guardan prompt, agente, identidad de carpeta y horario.
Admite una ejecución, intervalos y hora diaria con zona IANA. Los huecos DST se
omiten y la hora ambigua usa su primera aparición. Se coalescen vencimientos sin
ráfagas ni solapar la misma tarea. No hay parser cron universal ni wake de hardware.

Guardar/listar no inicia el programador. La activación revisa el hash del conjunto
completo, limitado a 32 tareas y prompts de 8000 bytes. SQLite reclama cada
ocurrencia una vez, con lease de runner único. Pausar, editar y retirar invalidan
claims no despachados. Lo que empezó conserva su snapshot; tras caída queda
interrumpido/incierto y no se repite automáticamente.

El despacho crea una conversación propia y usa el Runtime normal: las mutaciones
siguen pidiendo al dueño su aprobación en la app. Telegram u otra fuente textual
no pueden concederla. El historial enlaza la conversación y sus recibos. Cambiar
o sustituir físicamente la carpeta impide inferencia/despacho. Si falla guardar el
vínculo del run, se cancela y asienta antes de cerrar la ocurrencia; no queda una
tarjeta tardía capaz de actuar para una tarea ya fallida.

El resultado del modelo por sí solo tampoco completa una tarea. El despacho
espera al observador de persistencia de su propio run y exige un recibo terminal
en SQLite. Si falla esa escritura, la ocurrencia queda interrumpida e incierta,
conservando su run ID; no se reintenta ni se anuncia una finalización conocida.

## Conexiones nativas

Una confirmación abierta durante mucho tiempo podía dejar un socket en caché que
el servidor ya había cerrado por inactividad. El cliente ahora renueva conexiones
inactivas antes de escribir una solicitud y vuelve a comprobar permisos y token.
Nunca repite una solicitud después del envío. Un rechazo de credencial permanece
bloqueado hasta una reconexión explícita. Las pruebas cuentan tanto solicitudes
como efectos en un servidor local real, incluyendo mutaciones de una sola vez.

El cierre MCP distingue el PID del programa de su grupo de procesos. Una señal
denegada produce `mcp_cleanup_incomplete`: retira el catálogo, conserva el HOME
privado y los propietarios de recursos y permite intentar otro cierre explícito.
No reconecta ni repite llamadas. La salida del padre no acredita la ausencia de
descendientes; antes de liberar recursos se exige comprobar que el grupo ya no
existe. Un fallo MCP tampoco impide cerrar los procesos aislados del workspace,
y el servicio conserva SQLite hasta poder completar su limpieza.
Esta comprobación cubre el grupo heredado seleccionado, no procesos que un
programa separe de él. Un programa MCP elegido por el dueño sigue sin ser una
sandbox del sistema operativo.

## Verificación y límites

Las pruebas usan SQLite, sockets, archivos y procesos del sistema reales con datos,
proveedores y decisiones sintéticos. Los resultados automatizados finales y los
recorridos con la cuenta oficial y el bundle concreto se registran por
separado en `PHASE_A_MANAGEMENT_QUALIFICATION.json`.

Los scripts de escritorio retiran sus cachés de compilación después de un éxito
y conservan el bundle solicitado. Ante fallo mantienen el directorio de diagnóstico.
Se recuperaron archivos de iCloud marcados `dataless` antes de repetir los controles;
el informe distingue ese estado del entorno de los defectos de cierre reproducidos.

Siguen abiertos, entre otros, PTY, agentes delegados, biblioteca/paquetes remotos,
cron completo, navegador/escritorio, voz, conectores y distribución autónoma. No
se presentan las familias completas como cerradas por probar estos subconjuntos.
