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
./script/install_jarvis_cli.sh
jarvis
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

Una consulta no interactiva se ejecuta así:

```bash
jarvis engineer \
  --domain backend \
  --request "Revisa los límites de concurrencia de la API"
```

Desde una carpeta situada dentro de la raíz autorizada también puedes fijar el proyecto actual con
`--workspace .`. Fuera de esa frontera, el argumento se rechaza de forma segura.

El daemon conserva su frontera de confianza. Para trabajar en otro repositorio, su LaunchAgent debe
haber sido instalado con ese directorio como `AEGIS_WORKSPACE_ROOT`; un cliente no puede ampliar esa
frontera mediante argumentos. Un path externo se rechaza antes de crear el trabajo.

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
/status
/new
/exit
```

`/new` separa la memoria conversacional de la tarea anterior. El GraphRAG durable continúa sujeto
a sus reglas de cifrado, relevancia y olvido.

## Coste, privacidad e Internet

La sesión comienza con `research=offline`. En ese estado las herramientas web se podan del prompt y
Jarvis no puede afirmar que verificó información actual. `public_web` habilita solamente lectura
HTTPS pública y acotada; no entrega cookies, sesiones del navegador ni secretos al buscador y exige
citar las URLs utilizadas.

`inference=hybrid` usa el enrutamiento local-first existente y permite escalar a un especialista
configurado cuando el modelo local no basta. `local_only` prohíbe la conmutación remota: si el motor
on-device no está disponible o no puede cumplir el contrato, la solicitud falla de forma explícita.

## Autorización

El CLI nunca confirma por sí mismo una acción crítica. Cuando el broker pide aprobación, la tarea
queda detenida y la aprobación se resuelve en la superficie confiable del notch/HUD mediante el
mecanismo de un solo uso existente. Cambiar de perfil, usar una frase imperativa o añadir texto al
prompt no concede capacidades.

## Evolución del modo ingeniería

La entrega inicial establece el transporte, las sesiones, el routing técnico, la salida Markdown,
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
