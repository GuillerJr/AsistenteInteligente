# Jarvis Engineering CLI

Jarvis tiene un solo núcleo y dos superficies complementarias:

- el **notch** mantiene la conversación personal, la voz, las aprobaciones y el estado vivo del
  enjambre;
- el **CLI** concentra trabajo técnico largo: arquitectura, frontend, backend, QA, DevOps,
  investigación y ciberseguridad defensiva.

No son dos asistentes. Ambos usan el mismo daemon autenticado, el mismo GraphRAG cifrado, las
mismas políticas, el mismo registro de auditoría y la misma actividad que anima el notch.

## Inicio

Instala el comando una vez y arranca la aplicación como de costumbre:

```bash
uv sync --group dev
./script/install_jarvis_cli.sh
jarvis --inference-policy local_only
```

Durante desarrollo también puedes usar `./script/aegis.sh engineer`; recorre exactamente el mismo
IPC sin depender de una instalación global.

Puedes ejecutar `jarvis` desde cualquier carpeta. Si no indicas `--workspace`, el CLI adopta la
raíz autorizada que publica el daemon; no usa accidentalmente tu carpeta personal como proyecto.
Usa `--workspace` solo para seleccionar explícitamente una subcarpeta dentro de esa raíz.

La interfaz interactiva detecta si está conectada a una terminal real. En ese caso presenta el
estado del núcleo, proyecto y políticas con color, muestra actividad antes del primer fragmento de
respuesta y diferencia visualmente errores y autorizaciones. Cuando la salida se redirige a otro
programa, elimina automáticamente colores y secuencias ANSI para conservar texto limpio.

El editor usa `prompt_toolkit` únicamente en sesiones interactivas: Enter envía, Alt+Enter inserta
una línea, Tab completa comandos y las flechas recuperan instrucciones anteriores. El pegado
multilínea se edita como una sola solicitud, no se ejecuta línea por línea. Ctrl-C descarta el
borrador o solicita cancelar la tarea activa; Ctrl-D sale. El historial del editor está limitado
a 100 entradas y 64 KiB, vive únicamente en memoria y omite material que parezca una credencial.
`NO_COLOR` desactiva los colores; `TERM=dumb` mantiene una presentación sencilla.

La salida del modelo conserva Markdown y código, pero neutraliza controles de terminal (ANSI,
OSC, caracteres bidi). Esto impide que un archivo o una respuesta borren la pantalla, modifiquen
el portapapeles o falsifiquen el orden visual mediante esos controles.

Una consulta no interactiva se ejecuta así:

```bash
jarvis engineer \
  --domain backend \
  --inference-policy local_only \
  --request "Revisa los límites de concurrencia de la API"

jarvis --request - --inference-policy local_only < consulta.txt
```

Si stdin no es una terminal, `jarvis` también lee una sola solicitud completa: las líneas `/new`
o `/exit` dentro de un archivo no se interpretan como comandos. El límite es 50 000 caracteres.
Solo la respuesta se escribe en stdout; avisos, estado y errores van a stderr. Códigos de salida:
`0` completado, `1` fallo, `2` argumentos inválidos, `3` aprobación pendiente en modo puntual,
`130` interrupción/cancelación y `141` cierre de la tubería de salida.

El origen procede de la evaluación del daemon, no del texto del modelo. «Respuesta recibida» y el
código `0` significan que terminó el transporte/trabajo, **no que la explicación técnica sea
correcta**. Las respuestas de modelos llevan ese aviso; no se infiere una prueba aprobada a partir
de su redacción ni del score de calidad conversacional.

Desde una carpeta situada dentro de la raíz autorizada también puedes fijar el proyecto actual con
`--workspace .`. Fuera de esa frontera, el argumento se rechaza de forma segura.

El daemon conserva su frontera de confianza. Para trabajar en otro repositorio, su LaunchAgent debe
haber sido instalado con ese directorio como `AEGIS_WORKSPACE_ROOT`; un cliente no puede ampliar esa
frontera mediante argumentos. Un path externo se rechaza antes de crear el trabajo.

Antes de razonar, el daemon construye un inventario de rutas local y acotado. El muestreo se reparte
entre los componentes superiores del proyecto (`src`, `native`, `tests`, `docs`, etc.) para que una
carpeta grande no oculte al resto. El inventario declara `observed_file_count` y `sample_complete`:
si la muestra está truncada, Jarvis tiene prohibido concluir que un componente ausente no existe.
Los nombres de archivo no se envían a un proveedor remoto. Cualquier afirmación sobre una
implementación concreta exige primero leer el archivo mediante la herramienta local confinada al
workspace.

## Perfiles

`/domain` cambia instrucciones, no permisos. Los perfiles disponibles son:

- `auto`: selecciona la disciplina más estrecha que exige el problema;
- `software`: implementación y mantenimiento general;
- `frontend`: UI, accesibilidad y rendimiento visual;
- `backend`: APIs, datos y concurrencia;
- `security`: análisis exclusivamente defensivo y autorizado;
- `devops`: builds y releases reproducibles;
- `qa`: reproducción, causa raíz y verificación;
- `research`: investigación técnica con trazabilidad;
- `architecture`: requisitos, límites y decisiones de sistema.

Comandos interactivos:

```text
/domain frontend
/research public_web
/inference local_only
/workspace "subcarpeta autorizada"
/status
/resume
/cancel
/new
/clear
/exit
```

`/status` consulta salud, integridad y disponibilidad local, además del proyecto, políticas,
conversación y tarea. `/resume` recupera la tarea identificada tras una desconexión, sin reenviar
la instrucción. No se acepta una segunda tarea encima de otra sin resolver. `/cancel` requiere una
respuesta autenticada antes de anunciar éxito; cancelar no revierte efectos ya realizados.
Si se pierde la respuesta durante el envío inicial, puede no conocerse el ID: revisa el HUD antes
de repetir una acción. El CLI no inventa que ese envío fue cancelado.

`/new` inicia otra conversación y limpia el historial del editor, pero **no borra** conversaciones
persistidas, GraphRAG ni preferencias. Las conversaciones se guardan mediante el mecanismo cifrado
existente; el historial temporal del editor es independiente. `/clear` solo limpia la pantalla.

## Coste, privacidad e Internet

La sesión comienza con `research=offline`. En ese estado las herramientas web se podan del prompt y
Jarvis no puede afirmar que verificó información actual. `public_web` habilita solamente lectura
HTTPS pública y acotada; no entrega cookies, sesiones del navegador ni secretos al buscador y exige
citar las URLs utilizadas.

`inference=hybrid` usa el enrutamiento local-first existente y permite escalar a un especialista
configurado cuando el modelo local no basta. `local_only` prohíbe la conmutación remota: si el motor
on-device no está disponible o no puede cumplir el contrato, la solicitud falla de forma explícita.

**`offline` no significa que toda inferencia sea local.** Para impedir tanto búsqueda web como
inferencia remota usa `--research-policy offline --inference-policy local_only`.

## Autorización

El CLI nunca confirma por sí mismo una acción crítica. Cuando el broker pide aprobación, la tarea
queda detenida y la aprobación se resuelve en la superficie confiable del notch/HUD mediante el
mecanismo de un solo uso existente. Cambiar de perfil, usar una frase imperativa o añadir texto al
prompt no concede capacidades.

La sesión interactiva sigue observando la tarea mientras decides en el HUD y muestra su resultado
cuando termina, con una pausa de 500 ms entre consultas de aprobación para evitar un bucle activo.
En modo puntual devuelve `3`, imprime el ID en stderr y no aprueba ni cancela automáticamente.

## Evolución del modo ingeniería

**Límite actual:** las herramientas del perfil de ingeniería permiten leer el repositorio y,
si lo autorizas, investigar fuentes públicas. Todavía no aplican parches, ejecutan tests arbitrarios
ni hacen commits. Una explicación o una propuesta de código no constituye un cambio ejecutado.

La entrega establece el transporte, las sesiones, el routing técnico, la salida Markdown,
el confinamiento de workspace y la investigación pública. La siguiente frontera se divide en
capacidades pequeñas y auditables:

1. inventario y búsqueda estructural del repositorio, siempre de solo lectura;
2. propuesta de parches con diff y aprobación explícita antes de escribir;
3. ejecutores de pruebas definidos por el repositorio, sin shell arbitrario;
4. revisión de diff, commits locales y publicación remota como permisos separados;
5. evaluaciones por disciplina y aprendizaje de procedimientos como skills versionadas, nunca como
   código auto-instalado.

Este orden evita convertir “aprender” en ejecución automática de contenido encontrado en Internet.
Jarvis puede investigar una técnica y preparar una capacidad, pero el dueño conserva la decisión de
instalarla, concederle permisos y activarla.
