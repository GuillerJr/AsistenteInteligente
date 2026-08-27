# Manual de instalación y configuración de Jarvis

Este es el manual único para instalar, configurar, validar, actualizar y diagnosticar Jarvis en un
Mac con Apple Silicon. Está escrito para que una instalación nueva pueda completarse copiando los
comandos en orden, sin descargar modelos NVIDIA ni guardar credenciales dentro del proyecto.

## 1. Qué se instala

Jarvis se divide en dos procesos locales:

- `ai.aegis.daemon`: núcleo Python que ejecuta LangGraph, memoria, políticas, auditoría y llamadas a
  las API remotas de NVIDIA NIM.
- `Jarvis.app`: aplicación macOS nativa que vive en Menu Bar y en el notch, captura voz local,
  muestra el HUD, ejecuta las acciones visuales autorizadas y aloja el helper on-device del cerebro
  local.

Ambos arrancan al iniciar sesión mediante LaunchAgents del usuario. La aplicación no aparece en el
Dock y el HUD solo se abre cuando el usuario lo solicita.

Los modelos NVIDIA de lenguaje, visión, embeddings y voz se consumen mediante API; no se descargan
ni se ejecutan modelos NVIDIA en el Mac. En macOS 26, Jarvis también puede usar el modelo de sistema
de Apple ya administrado por Apple Intelligence para conversación breve. Los otros modelos locales
son los que el usuario entrena para reconocer «Jarvis» y distinguir hablantes.

## 2. Requisitos

### Hardware y sistema

- Mac Apple Silicon nativo `arm64`; el proyecto está optimizado para el MacBook Air M5.
- macOS 14 Sonoma o posterior.
- Para la ruta conversacional on-device: macOS 26 y Apple Intelligence disponible. En sistemas
  anteriores Jarvis funciona con NVIDIA sin esa ruta.
- Conexión a Internet para NVIDIA NIM y las funciones de investigación web.
- Cuenta de NVIDIA Developer/NGC con acceso a los endpoints públicos.
- Cuenta con acceso al repositorio privado de GitHub.

### Herramientas

- Git.
- Homebrew nativo para Apple Silicon.
- Python 3.11 nativo. El código también admite 3.12 y 3.13, pero la instalación reproducible del
  proyecto usa `/opt/homebrew/bin/python3.11`.
- `uv` para crear y sincronizar el entorno Python.
- Apple Command Line Tools con Swift 6.2 o posterior.

Comprueba primero la arquitectura:

```bash
uname -m
```

El resultado obligatorio es `arm64`. Si aparece `x86_64`, cierra esa terminal y abre una terminal
nativa; no continúes bajo Rosetta.

Instala las Command Line Tools si todavía no existen:

```bash
xcode-select -p
```

Si el comando informa que no hay un directorio de desarrollo activo:

```bash
xcode-select --install
```

Instala Homebrew desde su [sitio oficial](https://brew.sh/) si aún no existe. Después instala las
dependencias:

```bash
brew install python@3.11 uv
```

Verifica las herramientas:

```bash
git --version
/opt/homebrew/bin/python3.11 --version
/opt/homebrew/bin/uv --version
/usr/bin/xcrun swift --version
```

Swift debe ser 6.2 o posterior.

## 3. Instalación rápida

Esta sección resume la instalación completa. Las secciones posteriores explican cada decisión y
las configuraciones opcionales.

```bash
git clone https://github.com/GuillerJr/AsistenteInteligente.git
cd AsistenteInteligente
/opt/homebrew/bin/uv sync --all-groups --no-editable \
  --python /opt/homebrew/bin/python3.11 \
  --cache-dir .uv-cache
```

Con la clave NVIDIA visible en la pantalla de NVIDIA, cópiala al portapapeles y ejecuta de
inmediato:

```bash
./script/aegis.sh import-nvidia-key
./script/aegis.sh doctor
./script/aegis.sh probe-nvidia
```

El primer comando almacena la clave en macOS Keychain y limpia el portapapeles. Nunca pegues la
clave en un comando, archivo `.env`, mensaje, commit o captura de pantalla.

Instala la identidad local estable, valida el proyecto y registra los servicios:

```bash
./script/local_codesign_identity.sh install
/opt/homebrew/bin/uv run --no-sync pytest
./script/test_native.sh
./script/daemon_service.sh install
./script/menu_bar_service.sh install
```

Solicita los permisos solo después de instalar la aplicación definitiva:

```bash
./script/menu_bar_service.sh permissions
./script/menu_bar_service.sh computer-permissions
```

Concede en Ajustes del Sistema los permisos descritos en la sección 8 y valida:

```bash
./script/daemon_service.sh status
./script/menu_bar_service.sh status
./script/aegis.sh daemon-status
```

Una instalación saludable muestra `status=ok`, `architecture=arm64`, `security=intact` y
`provider=configured`.

## 4. Obtener y guardar la API de NVIDIA

### 4.1 Crear la clave

Jarvis usa una clave personal que empieza por `nvapi-`. Hay dos rutas oficiales:

1. Abre [NVIDIA API Keys](https://build.nvidia.com/settings/api-key), inicia sesión y genera una
   clave para los endpoints serverless.
2. Si la cuenta usa NGC, abre [NGC Setup API Keys](https://org.ngc.nvidia.com/setup/api-keys), pulsa
   `Generate Personal Key`, incluye `NGC Catalog` y `Public API Endpoints` y genera la clave.

NVIDIA documenta la segunda ruta y permite seleccionar `Never Expire`. Si tu organización muestra
esa opción y necesitas una credencial sin fecha de vigencia, selecciónala. Una clave sin caducidad
debe rotarse manualmente si se sospecha exposición. Consulta la
[guía oficial de NVIDIA](https://docs.nvidia.com/rag/latest/api-key.html) para comprobar los nombres
vigentes de las opciones.

### 4.2 Importar la clave de forma segura

1. Copia la clave desde NVIDIA.
2. Sin pegarla en la terminal, ejecuta:

```bash
./script/aegis.sh import-nvidia-key
```

El resultado esperado es:

```text
nvidia_api_key=stored clipboard=cleared
```

Jarvis la guarda en el Keychain del usuario con estos metadatos:

| Campo | Valor |
| --- | --- |
| Servicio | `ai.aegis.nvidia-nim` |
| Cuenta | `default` |
| Valor | clave privada `nvapi-…` |

El comando no imprime la clave y vacía el portapapeles incluso si la importación falla.

### 4.3 Validar la clave

```bash
./script/aegis.sh doctor
./script/aegis.sh probe-nvidia
./script/aegis.sh probe-nvidia-embedding
./script/aegis.sh probe-nvidia-vision
./script/aegis.sh probe-nvidia-tts
./script/aegis.sh probe-nvidia-tools
```

`doctor` comprueba arquitectura, disponibilidad del embedding local y presencia de la clave. Si
informa `local_semantic_memory=unavailable`, reinstala la app; Jarvis seguirá recuperando mediante
FTS5 mientras tanto. Los `probe-*` realizan
solicitudes sintéticas mínimas y nunca muestran la credencial, un vector, audio privado o una
imagen del usuario. `probe-nvidia-tools` valida Function Calling pero no ejecuta herramientas.

### 4.4 Rotar o reemplazar la clave

Genera una clave nueva, cópiala y vuelve a ejecutar:

```bash
./script/aegis.sh import-nvidia-key
./script/aegis.sh probe-nvidia
./script/daemon_service.sh install
```

La entrada existente se reemplaza y el daemon se reinicia. Revoca después la clave anterior desde
NVIDIA.

## 5. Preparar el proyecto

### 5.1 Clonar

```bash
git clone https://github.com/GuillerJr/AsistenteInteligente.git
cd AsistenteInteligente
```

El repositorio es privado. Si Git solicita autenticación, inicia sesión con una cuenta autorizada o
usa el método de autenticación de GitHub ya configurado en el Mac.

### 5.2 Crear el entorno Python

```bash
/opt/homebrew/bin/uv sync --all-groups --no-editable \
  --python /opt/homebrew/bin/python3.11 \
  --cache-dir .uv-cache
```

El comando crea `.venv/` dentro del proyecto e instala versiones resueltas por `uv.lock`. No
instales las dependencias globalmente ni uses Rosetta, Docker o Conda para el núcleo local.

El wrapper `./script/aegis.sh` utiliza siempre `.venv/bin/python` y el código de `src/`, evitando que
se ejecute una copia antigua instalada en otro sitio.

### 5.3 Diagnóstico inicial

Ejecuta este paso después de importar la API:

```bash
./script/aegis.sh doctor
```

Resultado esperado:

```text
architecture=arm64
python=3.11.x
nvidia_base_url=https://integrate.api.nvidia.com/v1
nvidia_api_key=available
```

## 6. Firma local estable

macOS relaciona los permisos de privacidad con la identidad de la aplicación. Una firma ad hoc
cambiaría al recompilar y podría hacer que los permisos parecieran activados en Ajustes, pero no en
Jarvis. Para evitarlo, crea una sola identidad de desarrollo local:

```bash
./script/local_codesign_identity.sh install
./script/local_codesign_identity.sh status
```

La identidad se llama `Jarvis Local Development`, queda en el Keychain de inicio de sesión y solo
autoriza a `/usr/bin/codesign` a usar su clave privada. Es una identidad local para este Mac; no
sustituye un certificado `Developer ID Application` ni sirve para publicar la app.

El instalador es idempotente: si la identidad ya existe, la reutiliza. No la regeneres en cada
build. Si Keychain solicita confirmar confianza o acceso durante la primera instalación, autoriza
la identidad local de Jarvis.

## 7. Validar e instalar los servicios

### 7.1 Pruebas

```bash
/opt/homebrew/bin/uv run --no-sync pytest
./script/test_native.sh
./script/build_and_run.sh --verify
```

- `pytest` prueba el daemon, orquestación, seguridad, memoria y proveedores con transportes
  simulados; no consume la API real.
- `test_native.sh` ejecuta la suite Swift nativa.
- `build_and_run.sh --verify` compila, firma y abre un bundle temporal para una comprobación rápida.

La compilación incluye `jarvis-local-brain` y `jarvis-local-embedding` dentro de
`Jarvis.app/Contents/Helpers`. El daemon solo ejecuta helpers reales, ejecutables, propiedad del
usuario y sin escritura para grupo u otros. El segundo usa NaturalLanguage de macOS para búsqueda
semántica privada; si falta o falla, la memoria continúa mediante SQLite/FTS5.

### 7.2 Instalar el daemon

```bash
./script/daemon_service.sh install
./script/daemon_service.sh status
```

El servicio queda en `~/Library/LaunchAgents/ai.aegis.daemon.plist`, arranca al iniciar sesión y se
reinicia si falla. Sus logs están en `~/Library/Logs/Aegis/`.

### 7.3 Instalar Jarvis

```bash
./script/menu_bar_service.sh install
./script/menu_bar_service.sh status
```

El script:

1. Compila los productos Swift en modo Release.
2. Firma `Jarvis.app`, el entrenador de voz y `JarvisComputerHelper.app` con la identidad estable.
3. Instala el bundle en `~/Applications/Jarvis.app`.
4. Crea `~/Library/LaunchAgents/ai.aegis.menubar.autostart.plist`.
5. Abre Jarvis como aplicación de Menu Bar.

También genera `dist/Jarvis.zip`. El ZIP es un artefacto local; la publicación para otros Macs
requiere Developer ID y notarización.

## 8. Permisos de macOS

Jarvis nunca solicita permisos silenciosamente al arrancar. Cada permiso se pide mediante una
acción explícita y debe concederse al bundle instalado y firmado, no a una copia temporal.

### 8.1 Voz

```bash
./script/menu_bar_service.sh permissions
```

`permissions` avanza en orden por los cuatro permisos: Micrófono, Reconocimiento de voz, Pantalla y
Control. Se detiene cuando macOS necesita que tomes una decisión, por lo que nunca superpone dos
paneles. Jarvis pasa temporalmente al frente para que los diálogos de Micrófono, Speech, Pantalla y
Control no queden ocultos al arrancar desde Menu Bar; el helper de control aplica la misma regla.
También puedes pulsar cada tarjeta del panel de Jarvis para configurar solo ese permiso.

Concede:

- `Micrófono` a Jarvis.
- `Reconocimiento de voz` a Jarvis.

Rutas habituales:

- Ajustes del Sistema → Privacidad y seguridad → Micrófono.
- Ajustes del Sistema → Privacidad y seguridad → Reconocimiento de voz.

Jarvis consulta Micrófono con la API de grabación de macOS 14 o posterior. El estado mostrado
corresponde al permiso de audio real, no a una autorización de cámara o captura independiente.
El bundle firmado incluye el entitlement de entrada de audio requerido por hardened runtime.

La transcripción exige reconocimiento local de Apple. Jarvis no permite fallback remoto de Apple.

### 8.2 Pantalla y control del equipo

```bash
./script/menu_bar_service.sh computer-permissions
```

Concede:

- `Grabación de pantalla y audio del sistema` —o `Grabación de pantalla`, según la versión de
  macOS— a `Jarvis`.
- `Accesibilidad` —llamada `Control de dispositivos y acceso a los datos` en versiones nuevas— a
  `Jarvis`.
- Si aparece `JarvisComputerHelper` o `Jarvis Computer Control` en el panel de Control, activa
  también su entrada. El estado `CONTROL` solo será `LISTO` cuando el host y su helper estén
  autorizados.

El helper no mueve el cursor nativo. Jarvis dibuja su propio retículo y usa acciones accesibles
validadas. Las capturas se mantienen en memoria, pero durante una sesión aprobada se envían al
modelo de visión NVIDIA; la ventana de confirmación lo informa antes de ejecutar.

Jarvis consulta el estado cada segundo mientras la autorización está en curso. Al cerrar o abandonar
Ajustes del Sistema, se relanza automáticamente si el cambio requiere un proceso nuevo y vuelve a
leer TCC con la identidad firmada. No reemplaces `Jarvis.app` manualmente ni cambies su firma, porque
macOS puede crear una identidad de permiso distinta.

### 8.3 Automatización de aplicaciones

Mail, Calendario, Recordatorios y Contactos pueden mostrar una solicitud adicional de Automatización
la primera vez que una acción los necesite. macOS conserva esa decisión. Jarvis nunca lee el cuerpo
de los correos, notas de recordatorios, direcciones, cumpleaños ni notas de contactos. Toda acción
que modifica datos exige una confirmación de un solo uso. Puedes revisar o revocar el permiso en
**Ajustes del Sistema → Privacidad y seguridad → Automatización**.

### 8.4 Alertas proactivas locales

Activa **Alertas proactivas** en el menú de Jarvis. macOS solicitará **Notificaciones** y, para los
avisos de agenda, **Calendarios**. Si deniegas Notificaciones, Jarvis devuelve el interruptor a
apagado. Puedes cambiar después ambos permisos desde **Ajustes del Sistema → Notificaciones →
Jarvis** y **Privacidad y seguridad → Calendarios**.

Jarvis avisa de batería al 20 % y 10 %, pérdida/restauración de red, presión de memoria, presión
térmica y un evento próximo. No incluye el título del evento en la notificación. Cada clase tiene un
periodo de enfriamiento para evitar avisos repetidos. La implementación recibe eventos de macOS; no
mantiene un polling continuo. EventKit conserva un único temporizador para el próximo evento y una
revisión diaria solo cuando no hay eventos en los siguientes siete días.

## 9. Comprobación final

Ejecuta:

```bash
./script/local_codesign_identity.sh status
./script/daemon_service.sh status
./script/menu_bar_service.sh status
./script/aegis.sh daemon-status
./script/aegis.sh probe-nvidia
```

Comprueba visualmente:

- El icono template de Jarvis aparece en la Menu Bar.
- El panel muestra el core y NVIDIA disponibles.
- `MICRÓFONO` y `VOZ` aparecen autorizados.
- `CONTROL` aparece `LISTO` después de conceder pantalla y Accesibilidad.
- En un MacBook con notch se ve la presencia ambiental de Jarvis.
- `⌃⇧Espacio` emite un beep e inicia un turno de voz.

Estado de daemon esperado:

```text
status=ok protocol=1.0 architecture=arm64 security=intact provider=configured active_agents=0
```

`provider=configured` confirma que la credencial existe y tiene formato válido. `probe-nvidia`
confirma además conectividad y vigencia real.

## 10. Uso cotidiano

### Iniciar un turno de voz

Usa cualquiera de estas opciones:

- Pulsa `⌃⇧Espacio` desde cualquier aplicación.
- Abre Jarvis en la Menu Bar y pulsa `Hablar`.
- Desde terminal ejecuta `./script/menu_bar_service.sh voice-turn`.
- Después de entrenar y habilitar el wake word, di «Jarvis».

Para habilitar la escucha entrenada sin abrir el menú:

```bash
./script/menu_bar_service.sh wake-word-on
```

El sistema espera como máximo ocho segundos para que comiences a hablar. Una vez detectada la voz,
el turno termina tras 1,2 segundos de silencio o al alcanzar el límite defensivo total de 60
segundos. Al terminar de responder, Jarvis cierra la captura y vuelve a dormir. Para iniciar otro
turno debes decir «Jarvis» otra vez. El contexto se conserva, pero nunca se abre el reconocimiento de
voz automáticamente después de una respuesta.

Jarvis espera como máximo 1,8 segundos a que la voz NVIDIA comience. Si el proveedor tarda más,
arranca una voz española estándar de macOS, sin el tono grave artificial anterior y excluyendo voces
de personaje, para que la respuesta no permanezca bloqueada por la síntesis remota.
Cuando NVIDIA responde, el daemon entrega audio PCM incremental de 22,05 kHz por el IPC autenticado.
La app lo reproduce mientras todavía se sintetiza; no descarga un modelo, no guarda audio y no
recibe la API key. Una secuencia inválida, bloque mayor de 16 KiB o muestra incompleta se rechaza.
Cuando ya existe otra frase completa en cola, prepara solo esa frase mientras reproduce la actual.
El prefetch nunca excede 1 MiB, no sobrevive al turno y cierra su token aunque se interrumpa.

Antes de abrir el micrófono, Jarvis consulta una sola vez `runtime.preflight`. El daemon comprueba
en paralelo la auditoría, la credencial NVIDIA y el cerebro local; no conserva una copia temporal de
la integridad ni hace tres viajes IPC consecutivos. Si cualquiera de los campos es inválido, el turno
no comienza.

La respuesta comienza mientras el modelo todavía genera. Jarvis separa el stream únicamente en
frases completas para evitar palabras cortadas y nunca vuelve a pronunciar un fragmento ya emitido.
Si dices «Jarvis» mientras está procesando o hablando, cancela el trabajo y el audio actuales y abre
un turno nuevo. No es necesario esperar al final de la respuesta.

### Temporizador local por voz

Después de activar un turno, usa una orden completa con una cantidad numérica:

- «Pon un temporizador de 30 segundos».
- «Pon un temporizador de cinco minutos».
- «Configura un temporizador por 5 minutos».
- «Inicia un temporizador de 1 hora».
- «Estado del temporizador» o «Cuánto falta del temporizador».
- «Pausa el temporizador».
- «Reanuda el temporizador».
- «Cancela el temporizador».

Jarvis acepta desde un segundo hasta 24 horas y solo mantiene un temporizador activo. La confirmación
y el tiempo restante se expresan con horas, minutos y segundos naturales. La consulta no crea un
sondeo: calcula el remanente desde el deadline monotónico únicamente al pedirlo. La confirmación y
el aviso final usan la voz existente; al vencer siempre emite además un beep. Si Jarvis está
escuchando, procesando, hablando o esperando una aprobación, conserva ese turno y limita el aviso al
beep. El temporizador vive únicamente en memoria: cerrar, actualizar o reiniciar `Jarvis.app` lo
cancela. No usa NVIDIA, el daemon, red, memoria RAG ni un permiso nuevo, y no guarda la orden.
Pausar cancela la espera activa, conserva el remanente y sigue ocupando el único slot; reanudar crea
otra espera local por ese mismo tiempo. Ninguna de las dos operaciones crea un proceso o monitor.
Las cantidades escritas con palabras se admiten del uno al sesenta en español o inglés; fuera de ese
rango usa cifras. Fracciones, aproximaciones y cantidades vagas conservan el flujo normal.

### Aplicación activa por voz

Di `¿Qué aplicación estoy usando?`, `¿Cuál es la aplicación activa?` o
`What app am I using`. Jarvis consulta `NSWorkspace` dentro de la app nativa y responde con el nombre
de la aplicación en primer plano. El turno conserva el preflight normal de permisos, daemon e
integridad, pero no envía el transcript o el nombre mediante `voice.submit`. Tampoco captura pantalla,
enumera ventanas, lee documentos o usa modelos. El nombre se normaliza, se limita antes de hablar y
nunca llega a los logs; el bundle identifier tampoco se expone. Una pregunta compuesta conserva el
cerebro híbrido normal.

### Guía de capacidades por voz

Di `¿Qué puedes hacer?`, `¿Qué sabes hacer?` o `¿Cómo puedes ayudarme?`. Jarvis responde con un
resumen breve de conversación, memoria, consultas y acciones, y comprueba si Pantalla y Control
están listos antes de mencionar el control visual. La respuesta se construye dentro de la app, sin
modelo ni `voice.submit`, y no ejecuta o autoriza ninguna capacidad. Abrir aplicaciones, ejecutar
atajos y controlar visualmente una app conservan su aprobación de un solo uso y auditoría.

### Qué cerebro atiende cada solicitud

- Determinista/nativo: reloj, temporizadores, estado del Mac y órdenes inequívocas. Es siempre la
  primera opción y no usa modelos.
- Apple on-device: conversación breve y resumen posterior de lecturas públicas web o archivos;
  nunca selecciona ni ejecuta herramientas. Mail, Calendario, Recordatorios y Contactos se presentan
  mediante plantillas deterministas.
- NVIDIA NIM: código, ciberseguridad, razonamiento profundo, visión, contexto largo y planificación
  de herramientas que no sea determinista.
- Fallback: si Apple Intelligence no está disponible o el helper falla, el mismo turno pasa a
  NVIDIA. Broker y ejecutores locales conservan toda la autoridad sobre herramientas.

Una mención aislada de «hoy», «app», «clima», «precio» o «noticias» no carga herramientas. Jarvis
busca una orden explícita como «abre Safari», «revisa mi correo», «crea un evento», «busca…» o una
pregunta de información actual. Esto evita latencia remota durante una conversación casual, pero
no autoriza acciones: toda herramienta conserva su política y, cuando corresponde, la aprobación
de un solo uso.

Las siguientes órdenes inequívocas no necesitan una inferencia para preparar la aprobación:

- `Abre Safari` — también admite Chrome, Firefox, Calendario, Mail, Notas, Vista Previa, Xcode y
  Spotify mediante una lista local de bundle IDs conocidos.
- `Ejecuta el atajo Informe diario` — conserva literalmente el nombre y no admite rutas ni entrada.
- `Lista mis recordatorios` — devuelve título, lista, vencimiento y estado mediante lectura local.
- `Busca el contacto Ada` — busca nombre, correos y teléfonos localmente, sin enviar el resultado a
  NVIDIA.
- `Pon el volumen al 40 por ciento`, `Silencia el Mac` y `Activa el sonido` — cambian CoreAudio tras
  aprobar una vez.
- `Pausa la música`, `Siguiente canción` y `Canción anterior` — controlan una única instancia activa
  de Music o Spotify tras confirmación.
- `Busca en Spotlight Informe` — devuelve como máximo 10 nombres y tipos, nunca el contenido.
- `Abre con Spotlight Informe.pdf` — abre solo una coincidencia exacta, única y segura después de
  confirmar.
- `Revisa el estado de Git`, `Lista los procesos`, `Lista los puertos abiertos` y
  `Revisa la postura de seguridad` — seleccionan uno de los cuatro diagnósticos fijos existentes.

Jarvis muestra la aprobación pendiente igual que en el camino NVIDIA. No ejecuta nada antes de
`Aprobar una vez`. Si la frase contiene más de una acción, una negación, una aplicación desconocida,
un adjunto o audio no transcrito localmente, el atajo determinista no se usa.

Crear contactos o recordatorios y marcar un recordatorio como completado siempre muestra una
confirmación. La aprobación queda ligada a la acción y valores exactos; cambiar cualquier campo la
invalida. Completar también exige que exista una sola coincidencia exacta entre recordatorios
pendientes.

Spotlight se limita a tu carpeta personal y descarta `Library`, `.Trash`, nombres ocultos y enlaces
simbólicos. Si hay cero o varias coincidencias exactas, no abre nada. El control multimedia falla de
forma segura si Music y Spotify están activos al mismo tiempo o si ninguno está activo.

Para las órdenes que sí requieren NVIDIA, Jarvis no envía el catálogo completo. Una solicitud como
`Revisa mi correo` expone solamente `mail_list_recent`; `Crea un evento` solo
`calendar_create_event`; `Escanea mi red` solo `network_discover_hosts`; y `Controla Safari` solo
`computer_use`. Esto reduce tokens y evita que el modelo elija una capacidad ajena a la intención.
El broker verifica de nuevo el conjunto ofrecido y responde `tool_not_offered` ante cualquier
desviación. Si la intención operativa es ambigua, conserva el catálogo permitido del rol y todas las
políticas habituales.

Las lecturas privadas siguientes tienen un camino local adicional:

- `Revisa mi correo` o `Muéstrame mis correos no leídos`: consulta como máximo 10 metadatos del
  inbox. Nunca recupera cuerpos.
- `Tengo correos no leídos` o `Tengo algún correo sin leer`: comprueba como máximo un resultado y
  responde sí o no sin invocar planificación ni síntesis por modelo.
- `Cuál es mi último correo` o `Dime mi último correo`: consulta un único mensaje y presenta
  remitente, asunto y fecha local sin recuperar el cuerpo ni usar un modelo.
- `Qué tengo hoy`, `Revisa mi agenda` o `Revisa mi calendario`: consulta como máximo 20 eventos
  entre las 00:00 y 24:00 de la zona horaria local.
- `Tengo eventos hoy` o `Tengo algo en el calendario hoy`: comprueba como máximo un evento del día
  local y responde sí o no sin invocar ningún modelo.
- `Qué tengo mañana` o `Revisa mi calendario de mañana`: usa el mismo límite para el siguiente día
  local exacto.
- `Cuál es mi próximo evento` o `Qué sigue en mi calendario`: devuelve el primer evento cronológico
  desde el instante actual, con un horizonte máximo de 31 días. Título, fecha y hora se formatean
  localmente con el offset aplicable al día del evento, sin sintetizador.

La llamada de lectura se construye sin NVIDIA y Mail/Calendario se presentan con plantillas locales:
como máximo cinco elementos y un contador del resto. Un resultado válido, error o estructura
inválida no se envía a Apple Intelligence ni NVIDIA. Las restricciones TCC y la auditoría siguen
activas. Pasado mañana, días de semana, rangos y expresiones compuestas vuelven al planner para no
elegir un intervalo incorrecto; la presentación del resultado continúa local. La búsqueda del
próximo evento ordena candidatos entre todos los calendarios antes de devolver uno y falla de forma
cerrada si el volumen excede el tope interno.

El mismo gestor de aprobación ejecuta después de `Aprobar una vez` correo, calendario, contactos,
recordatorios, CoreAudio, multimedia, Spotlight, aplicaciones, navegador y atajos. Valida el
contrato devuelto y los valores ligados a la autorización antes de mostrar una respuesta
determinista; una salida incoherente marca el trabajo como fallido.

La investigación web inequívoca también tiene un camino rápido:

- `Busca noticias de NVIDIA NIM` o `Investiga seguridad en Apple Silicon`: consulta DuckDuckGo y
  lee como máximo tres páginas públicas.
- `Lee https://example.com/report`: extrae como máximo 8.000 caracteres de texto público.
- `Abre https://example.com/report`: prepara la URL sin inferencia, pero exige `Aprobar una vez`
  antes de abrir el navegador predeterminado.

Solo se acepta HTTPS público. Jarvis rechaza credenciales dentro de la URL, puertos no estándar,
destinos locales o privados, contenido binario y más de tres redirecciones. Estas lecturas no usan
cookies ni la sesión abierta de Safari o Chrome. Los resultados intentan resumirse con Apple
Intelligence on-device; NVIDIA nunca recibe el resultado de una herramienta, aunque el cerebro
local no pueda iniciar.
Una orden compuesta o una frase fuera de estas gramáticas exactas vuelve al planner normal.

Para una lectura local breve usa una ruta relativa al workspace configurado:

- `Lee el archivo README.md`
- `Lee el archivo docs/architecture/ADR-0093-local-public-web-research.md`
- `Read file notes/daily brief.txt`

Jarvis lee como máximo 8 KiB de un archivo UTF-8 regular y resume el resultado con Apple
Intelligence on-device. Rechaza rutas absolutas, `..`, escapes del workspace y enlaces que apunten
fuera de él; durante la apertura no sigue enlaces. El contenido se considera no confiable: una
instrucción escrita dentro del archivo nunca autoriza acciones. Si el cerebro local no puede iniciar,
Jarvis informa el fallo sin sacar el fragmento del Mac. Usa `Revisa el archivo …`
cuando quieras análisis de código o ciberseguridad; esa frase permanece en el especialista NVIDIA y
no entra en el atajo de lectura literal.

Una frase no exacta, como `Revisa mi correo reciente`, todavía usa NVIDIA una vez para escoger y
parametrizar la lectura. Después de que el broker y el ejecutor local terminan, el resultado de Mail,
Calendario o web se resume con Apple Intelligence. Así los metadatos privados no salen del Mac en el
caso normal y se elimina la segunda ronda NVIDIA. Si Apple no está disponible antes del primer
fragmento, Jarvis conserva el resultado local y explica que no pudo sintetizarlo. Las lecturas
solicitadas por el especialista de código/ciberseguridad no cambian de cerebro.

Cuando el resultado válido está vacío, Jarvis responde directamente: no hay mensajes, eventos,
resultados públicos, texto legible o contenido de archivo. No se invoca Apple Intelligence ni un
synthesizer NVIDIA para redactar esa ausencia. Una respuesta malformada o una lectura no vacía vuelve
al camino normal; el atajo nunca convierte un error de acceso en “no encontré nada”.

Los errores conocidos se explican igual de rápido y sin inferencia: archivo inexistente, texto que no
es UTF-8, permiso denegado, timeout, acceso web fallido o error local de I/O. La frase no muestra la
ruta, URL, query ni argumentos originales. Un código nuevo o inconsistente vuelve al synthesizer en
vez de inventar una explicación. Las denegaciones del broker siguen siendo decisiones separadas y no
se disfrazan como fallos de ejecución.

Para consultar el equipo real sin inferencia, di `Describe este Mac`, `¿Qué Mac tengo?` o
`What hardware does this Mac have`. Jarvis obtiene arquitectura, versión de macOS y Python; en macOS
también intenta leer chip, modelo y memoria con `/usr/sbin/sysctl`. La orden es fija, no usa shell,
tiene timeout de un segundo y no requiere red, NVIDIA, Apple Intelligence ni permisos nuevos. Si los
campos de hardware no están disponibles, responde con los metadatos base en vez de bloquearse.

Para conocer la energía actual di `Estado de la batería`, `¿Cuánta batería queda?` o
`¿Cómo está la batería?`. Jarvis ejecuta una única consulta fija `/usr/bin/pmset -g batt`, valida y
reduce la salida localmente y responde sin Apple Intelligence ni NVIDIA. Nunca expone el ID interno
de la batería y no solicita permisos: informa únicamente porcentaje, carga o descarga, fuente de
energía y, cuando macOS la ofrece, autonomía estimada. La consulta ocurre solo al pedirla; no instala
un monitor ni hace *polling* energético.

Para conocer el espacio disponible di `¿Cuánto almacenamiento queda?`,
`¿Cuánto espacio libre queda?` o `Estado del almacenamiento`. Jarvis consulta con `statvfs` el
volumen de inicio y responde directamente con capacidad total, disponible y porcentaje libre. No
enumera archivos, carpetas, otros volúmenes, snapshots o identificadores; tampoco usa procesos,
shell, memoria, Apple Intelligence, NVIDIA, permisos nuevos ni monitor residente. En APFS la cifra
puede variar respecto a Finder por espacio purgable y snapshots administrados por macOS.

Jarvis también puede describir tres señales operativas sin modelos ni permisos nuevos:

- `Estado del audio` o `¿El Mac está silenciado?`: informa volumen de salida y silencio mediante
  CoreAudio. No inicia, consulta ni devuelve el dispositivo de entrada.
- `Estado de la red` o `¿Tengo conexión de red?`: confirma únicamente conectividad de red local y
  disponibilidad de IPv4/IPv6. No revela IP, MAC, SSID o nombre de interfaz y no garantiza que
  Internet sea alcanzable. Para comprobar Internet se necesita una lectura web explícita.
- `Estado del rendimiento` o `Carga del sistema`: informa carga media de un minuto frente a los
  núcleos lógicos y memoria disponible agregada. No enumera procesos ni conserva muestras.

Estas lecturas se ejecutan solo al solicitarlas. Red y memoria usan comandos absolutos con timeout de
dos segundos; audio llama directamente a CoreAudio sin abrir un proceso. Una salida malformada o
fuera de límites falla cerrada y nunca se envía a Apple Intelligence o NVIDIA para interpretarla.

Para consultar el reloj local di `¿Qué hora es?`, `¿Qué fecha es hoy?` o
`Dime la fecha y hora`. Jarvis usa la fecha, hora y zona horaria del proceso local, responde en
español con formato de 24 horas y no invoca memoria, herramientas, Apple Intelligence o NVIDIA.
Consultas como `¿Qué hora es en Tokio?` no usan este atajo porque requieren interpretar otra zona.
`¿Cuál es mi zona horaria?` informa el offset UTC actual, incluidos offsets de media hora o 45
minutos, sin inferir una ciudad.

Para consultar el tiempo desde el arranque di `¿Cuánto tiempo lleva encendido este Mac?`,
`Tiempo activo del Mac` o `System uptime`. Jarvis lee `CLOCK_MONOTONIC_RAW`, que en macOS continúa
durante reposo y no cambia por ajustes del reloj civil. Redondea hacia abajo a minutos, no instala
un monitor y no usa permisos, procesos, memoria ni modelos. Si el reloj no está disponible, responde
con un fallo local en vez de escalar a NVIDIA.

Para cálculos breves di `Calcula 12,5 por 4`, `¿Cuánto es doce por cuatro?`,
`¿Cuánto es 25 más 17?` o
`Calcula 144 dividido entre 12`. Jarvis admite exactamente dos números decimales y uno de estos
operadores: suma, resta, multiplicación o división. El cálculo usa `Decimal` local, nunca `eval`, y
responde sin memoria ni modelos. Una división por cero recibe una negativa directa; potencias,
porcentajes, expresiones encadenadas y números fuera de los límites pasan al cerebro normal. Los
enteros hablados se aceptan de −100 a 100 en español o inglés; los decimales hablados no se infieren.

Comprueba la disponibilidad observada por el daemon:

```bash
./script/aegis.sh daemon-status
```

`local_model=available` significa que el modelo de sistema aceptó el probe local. No revela datos
del modelo ni contenido conversacional.

### Enseñar preferencias a Jarvis

Exprésalas de forma directa: «Me gusta el jazz», «Prefiero respuestas breves», «Me interesa la
astronomía», «Trabajo como desarrollador» o «Mi nombre es Guillermo». Jarvis guarda el dato después
de completar el turno, dentro de la base privada local, y lo aplica sutilmente en conversaciones
posteriores. No ejecuta otra llamada al modelo para aprender.

Cada hecho guarda evidencia (`explicit_text` o `verified_voice`), confianza y fecha de última
confirmación. Expresiones temporales como «por ahora» o «esta semana» caducan a los 14 días y dejan
de recuperarse automáticamente. Jarvis no eleva una inferencia silenciosa a preferencia confirmada.

Puedes preguntar «¿Qué sabes de mí?», eliminar un dato con «Olvida que me gusta el jazz» o borrar
todo el perfil con «Olvida todo lo que sabes de mí». Esto no borra el historial de conversaciones ni
otras memorias creadas manualmente. Una afirmación hablada solo modifica el perfil cuando el modelo
contiene un único perfil y lo reconoce con confianza suficiente. Con varios perfiles, el aprendizaje
por voz falla cerrado hasta que exista una selección explícita de propietario; la identidad sigue
sin autorizar acciones sensibles.

### Corregir el estilo de Jarvis

Las correcciones de estilo son órdenes explícitas y se aplican desde el turno siguiente:

- `Sé más breve` o `Dame más detalle` cambia únicamente el nivel de detalle.
- `Háblame más natural`, `Sé más directo` o `Háblame con más calidez` cambia únicamente el tono.
- `No termines con preguntas` desactiva preguntas finales salvo que falte una aclaración necesaria.
- `Puedes hacer preguntas de seguimiento` permite una sola cuando aporte continuidad.
- `No repitas` evita reiterar información que el propietario ya comprendió.
- `Restablece tu estilo` elimina estas preferencias sin borrar nombre, intereses o historial.

Jarvis conserva una ranura por dimensión. Por eso pedir después `Dame más detalle` reemplaza
`Sé más breve`, pero mantiene `Háblame más natural` y `No repitas`. Si una sola frase pide a la vez
brevedad y detalle, esa dimensión no se aprende. Una pregunta como `¿Puedes ser más breve?` tampoco
se interpreta como preferencia permanente.

En voz, el feedback exige el único perfil local reconocido con confianza suficiente. Las etiquetas
se traducen a un conjunto fijo de instrucciones para Apple Intelligence; el contenido de memoria no
se eleva a instrucción y NVIDIA no recibe estilo, perfil ni identidad. El turno de corrección se
responde mediante una plantilla local, sin Apple Intelligence ni NVIDIA; una voz no verificada
recibe una negativa y no modifica el perfil. La compuerta de calidad de `self-evaluation` permite
comprobar después si disminuyen repetición, exceso y aperturas mecánicas.

### Calificar la respuesta anterior

Di `Esa respuesta fue útil` o `Esa respuesta no fue útil` para calificar el último job completado
de la misma conversación. Jarvis responde mediante una plantilla local; no llama a Apple
Intelligence ni NVIDIA. En voz exige el propietario reconocido. Una pregunta, un comentario ambiguo
como `bien` o una voz no verificada no modifican la evaluación.

Solo se persiste `helpful` o `unhelpful` en el SQLite privado de evaluaciones. No se copia la
respuesta, solicitud, identidad o audio. El acuse se marca como evento de feedback y queda fuera de
la latencia y calidad conversacional para no mejorar artificialmente las cifras. Un feedback
cancelado tampoco se aplica. La asociación vive en el daemon y está limitada a la conversación
actual; si no existe un job reciente o el servicio se reinició, Jarvis informa que no encontró un
objetivo y no adivina.

Cuando la calificación es `Esa respuesta no fue útil`, Jarvis pide la corrección y reserva el
siguiente turno de esa conversación como reparación durante un máximo de dos minutos. Basta con
decir lo que se necesitaba, por ejemplo: `Necesitaba un resumen ejecutivo`. El cliente de voz
conserva el mismo identificador de conversación, por lo que el cerebro local ya dispone del
intercambio anterior. La ventana se consume al enviar ese turno, caduca sin polling y desaparece al
reiniciar el daemon; no modifica gustos ni estilo permanente. Una calificación útil la cancela.

La señal de reparación y el historial se entregan exclusivamente a Apple Intelligence. Si el
modelo local no está disponible, el fallback NVIDIA recibe solo la solicitud minimizada y la
política conversacional normal: no conoce el veredicto, la ventana ni el historial privado.

Para confirmar que Jarvis corrigió el problema, califica inmediatamente la reparación con
`Esa respuesta fue útil` o `Esa respuesta no fue útil`. La evaluación guarda únicamente que el
turno fue un intento de reparación y el enum del veredicto; no crea otro registro de texto.

### Conversar con continuidad y gestionar compromisos

Jarvis decide localmente si el turno es una tarea, conversación, petición de apoyo, consejo, lluvia
de ideas o corrección de un malentendido. La clasificación no se guarda como estado emocional ni
usa otro modelo. Solo ajusta la forma de responder: por ejemplo, una corrección se reconoce antes de
continuar y una expresión de frustración recibe atención antes de una posible solución. Jarvis sigue
identificándose como IA y no fomenta exclusividad o dependencia emocional.

La sesión de voz mantiene contexto durante 30 minutos desde el último turno enviado. Queda ligada
al propietario elegido o resuelto por el modelo, a la huella del Core ML exacto y a una prueba
reciente de presencia del dueño del dispositivo. Esta prueba vive solo en RAM durante 30 minutos
desde el último desbloqueo o desde una autenticación nativa. Arrancar o reiniciar Jarvis no concede
presencia por sí solo. No se renueva por hablar ni se guarda en `UserDefaults`.

Cuando vence, el siguiente turno reconocido como propietario muestra el diálogo de macOS para
Touch ID o contraseña. Si eliges `Continuar sin memoria`, cancelas o la autenticación falla, Jarvis
sigue respondiendo en una conversación aislada: no expone historial, memoria, gustos ni contexto
relacional y no reemplaza la sesión privada. La misma regla cubre una consulta hablada que adjunta
una captura de pantalla. Un perfil único ya no concede contexto privado por sí solo.

Al bloquear o abandonar la sesión de macOS, Jarvis revoca inmediatamente la presencia, cancela la
captura, la voz, el job y el control en curso, detiene la activación y oculta su puntero. Después de
desbloquear puede reanudar el detector si el opt-in, los permisos, el modelo y el daemon siguen
disponibles. Este flujo no cambia los permisos TCC ni concede autorización para acciones externas.
Cada sesión activa usa una generación local distinta: una respuesta, aprobación u orden iniciada
antes del bloqueo nunca recupera validez al desbloquear. Si el daemon devuelve tarde un `job_id`,
Jarvis lo cancela automáticamente.

Si pasan 30 minutos, la próxima solicitud crea una conversación nueva automáticamente. No existe un
temporizador en segundo plano: la app comprueba la fecha local justo antes de enviar. Un UUID sin
fecha, un reloj retrocedido o un estado incompleto también fuerzan una sesión nueva. Solo se guardan
en `UserDefaults` el UUID, la fecha y, cuando corresponde, el identificador acotado del perfil y la
huella SHA-256 del modelo local; no se guardan audio, embeddings ni confianza.

La creación o recuperación ocurre dentro del mismo `voice.submit` o `image.submit`; Jarvis no hace
una llamada previa ni reenvía la captura. El daemon devuelve el UUID definitivo únicamente cuando
la voz propietaria y la presencia local están verificadas. Una voz invitada sigue respondiendo de
forma efímera y no crea una conversación en la base local.

Para separar temas de inmediato di `Jarvis, nueva conversación`, `Jarvis, inicia una conversación
nueva` o `Jarvis, empecemos una conversación nueva`. Es una orden local exacta: no llama a Apple
Intelligence ni NVIDIA. La rotación no borra el historial anterior, gustos, perfil, temas o
compromisos; simplemente impide que los turnos de la sesión anterior se adjunten a la siguiente. Si
la sesión está ligada a un perfil, una voz sin identidad y presencia local verificadas no puede
rotarla. La identidad vocal sigue
siendo una señal falible de privacidad y personalización: no autentica, autoriza herramientas ni
reemplaza las confirmaciones visibles.

Para mantener continuidad explícita puedes decir:

- `Estoy trabajando en Jarvis` o `Mi proyecto actual es Jarvis` para conservar un tema 90 días.
- `Recuerda que debo terminar la prueba de voz` para conservar un compromiso 30 días.
- `Marca como resuelto terminar la prueba de voz` para retirar ese compromiso exacto.
- `Olvida el tema Jarvis` para retirar un tema exacto.
- `Olvida mis compromisos` o `Olvida mis temas y compromisos` para limpiar esas categorías.

Se guardan como memorias episódicas en el mismo SQLite privado, solo después de un turno exitoso.
Una orden hablada necesita el perfil de propietario verificado y una presencia local reciente.
Jarvis no deduce compromisos
de conversaciones ambiguas, no conserva perfiles emocionales y no envía este contexto a NVIDIA.

### Abrir el HUD

```bash
./script/menu_bar_service.sh hud
```

También puedes elegir `Mostrar HUD…` en la Menu Bar. El HUD es transparente, no se abre al iniciar
sesión y muestra qué clústeres del enjambre están activos.

### Controlar aplicaciones o navegador

Pide el objetivo por voz. Si Jarvis propone una acción externa, abrir una URL, enviar correo, crear
un evento o controlar visualmente una app, aparecerá una aprobación pendiente. Revisa el resumen y
elige `Aprobar una vez` o `Denegar`.

Para aplicaciones compatibles con Atajos de macOS, crea primero el atajo en la app Atajos y pide:
«Jarvis, ejecuta el atajo Informe diario». Jarvis solo acepta su nombre exacto, no archivos de
entrada ni argumentos de terminal, y solicita aprobación de un solo uso antes de invocarlo.

El control visual está limitado deliberadamente. No opera Passwords, Keychain, Ajustes del Sistema,
gestores de contraseñas, pagos, logins, descargas, borrados, campos seguros ni atajos destructivos.
No controla Terminal, Finder o Mail mediante visión; esas capacidades usan herramientas específicas
y políticas separadas.

En una sesión aprobada, Jarvis analiza primero la aplicación autorizada de forma local. Vision
reconoce hasta 16 fragmentos de texto y Accessibility recorre hasta 256 elementos para extraer
ventanas, botones, enlaces y campos. Si una orden exacta como `Pulsa el botón Documentación`
coincide con un único control no sensible, el helper ejecuta `AXPress` sin enviar la captura a
NVIDIA. Si hay ambigüedad, el rol de visión recibe la captura acotada junto con el resumen local. Un
campo seguro o texto asociado a login, pago, envío, descarga, borrado o permisos bloquea la sesión
antes de cualquier envío remoto. El OCR solo aporta percepción; nunca autoriza una acción.
La captura contiene únicamente las ventanas de esa aplicación: macOS excluye barra de menús,
cursor, audio, escritorio, puntero de Jarvis y cualquier otra app.
Jarvis fija la instancia mediante bundle ID, PID y fecha de lanzamiento. ScreenCaptureKit captura
solo el proceso exacto y el recorrido Accessibility descarta cualquier ventana o elemento cuyo PID
no coincida. La identidad vuelve a comprobarse tras la captura y al terminar OCR/percepción local;
si la aplicación se cierra o relanza, esa observación no se entrega al modelo.
En equipos con varios monitores, Jarvis no presupone que la app vive en la pantalla principal.
Selecciona el display con mayor intersección respecto a la ventana Accessibility enfocada y usa sus
mismos límites para captura, OCR, coordenadas de clic y scroll. Una ventana repartida entre dos
monitores queda ligada al de mayor área visible; un empate se resuelve de forma determinista. Si la
ventana deja de intersectar un display activo o cambia de display durante la observación, la sesión
falla cerrada y no ejecuta la acción.
Una aplicación congelada tampoco retiene el ciclo indefinidamente. El helper limita cada consulta
AX perceptual a 150 ms y deja de iniciar consultas al superar un presupuesto monotónico de 500 ms;
en ese caso informa percepción truncada y conserva solo el OCR ya disponible. Una acción dispone de
un segundo por llamada AX y no se repite automáticamente si macOS informa que no pudo completarla.

El contrato de teclado permite sin modificadores únicamente Escape, Tab, flechas, Inicio, Fin y
Página arriba/abajo. Enter, Espacio y letras sueltas se rechazan antes del helper para impedir envíos
accidentales o escritura carácter por carácter; los únicos atajos son `⌘A/F/L/R/T` y `⇧Tab`. Python,
el decoder Swift y el ejecutable nativo aplican la misma lista. Captura, OCR, títulos y textos
Accessibility se tratan como datos no confiables, y controles bidi/no imprimibles fallan cerrados.

Los cinco atajos con Command tienen un camino local exacto: `Selecciona todo` (`⌘A`), `Buscar en la
página` (`⌘F`), `Enfoca la barra de direcciones` (`⌘L`), `Recarga la página` (`⌘R`) y `Abre una
pestaña nueva` (`⌘T`). También aceptan sus equivalentes directos en inglés. La frase debe coincidir
completa; `Recarga la página y abre otra pestaña` no ejecuta la primera mitad. Jarvis no envía la
captura a NVIDIA, pero conserva la aprobación de un solo uso, el contexto visual y la recaptura de
progreso posterior.

Para buscar texto dentro de la página sin inferencia, di `Busca «arquitectura segura» en la página`
o `Find "release notes" on page`. Jarvis exige presupuesto para dos pasos antes de comenzar. Primero
ejecuta `⌘F` y recaptura; únicamente si el panel produjo progreso verificable y no reveló contenido
sensible escribe el literal exacto. No pulsa Enter. Si la primera acción no cambia la interfaz, el
texto nunca se entrega y la sesión termina como incierta.

Para escribir sin inferencia en el campo ya enfocado, usa una orden literal cerrada:
`Escribe «Hola, mundo»` o `Type "release status: ready"`. Jarvis conserva exactamente Unicode,
espacios internos y puntuación entre las comillas. Comillas vacías, texto con espacios exteriores,
órdenes sin comillas o delimitadores incompletos no entran en este fast-path. Palabras que identifican
contraseñas, PIN, tokens, secretos, claves API o tarjetas bloquean localmente la operación antes de
contactar al proveedor. La aprobación de control visual de un solo uso sigue siendo obligatoria.

También puedes indicar el campo sin recurrir a NVIDIA: `Escribe «informe» en el campo «Buscar»` o
`Type "report" in the "Search" field`. Jarvis exige un único `ComboBox`, `SearchField`, `TextArea` o
`TextField` Accessibility no sensible cuya etiqueta coincida. Lo enfoca con `AXFocused`, recaptura y
solo escribe cuando macOS confirma que ese mismo elemento recibió el foco. Si ya estaba enfocado,
omite ese efecto. OCR, campos seguros, árboles truncados y coincidencias ambiguas no autorizan la
secuencia; tampoco mueve el cursor del usuario.

Para sustituir contenido existente de forma explícita usa
`Reemplaza el contenido del campo «Buscar» por «arquitectura segura»` o
`Replace the contents of the "Search" field with "secure architecture"`. Esta variante no simula
`⌘A`: realiza una única asignación local de `AXValue` sobre el campo Accessibility inequívoco. El
helper compara PID, coordenadas, descriptor completo, ventana, contador de entrada física y permiso;
después vuelve a leer el valor y el daemon recaptura la app para comprobar progreso semántico. No usa
portapapeles, puntero ni NVIDIA. Un campo de contraseña, sensible, ambiguo, truncado o no modificable
se bloquea sin escritura parcial.

Al escribir, Jarvis fija el campo Accessibility enfocado al inicio y lo vuelve a comprobar antes de
cada fragmento: la aplicación debe seguir al frente, el elemento debe ser el mismo, su rol debe
seguir siendo editable y no puede volverse seguro o sensible. Un cambio de foco detiene los
fragmentos restantes. La división conserva grafemas Unicode completos, incluidos emojis y acentos,
sin usar portapapeles ni persistir el texto.

El helper fija además el PID exacto de la aplicación al comenzar cada acción. Escritura, teclas y
scroll se entregan directamente a ese PID con la API pública de CoreGraphics y una fuente privada;
no se publican como entrada HID global. Si la app cambia, termina o se reinicia, Jarvis no continúa
contra el nuevo proceso. Todo campo, ventana o control Accessibility debe pertenecer al mismo PID,
incluso cuando conserva el mismo bundle ID.

La observación lleva internamente un contexto visual SHA-256 de 64 caracteres ligado al bundle ID,
PID, fecha de lanzamiento, display y geometría de la ventana enfocada. Jarvis lo conserva fuera del
prompt y lo entrega automáticamente al helper con la siguiente acción. Si la ventana se mueve,
cambia de tamaño, pasa a otro monitor o la app se relanza, el contexto ya no coincide y no se ejecuta
el primer clic, foco, texto, tecla o scroll. El daemon recaptura una sola vez localmente y vuelve a validar
la misma acción únicamente cuando proviene del fast-path determinista de una orden local exacta; solo
la reintenta con el contexto nuevo si el objetivo Accessibility sigue siendo exacto y no apareció
contenido sensible. No vuelve a consultar NVIDIA. Otro cambio local, un botón distinto o percepción
segura se detienen como estado incierto o sensible. Para una decisión remota, Jarvis nunca repite la
acción obsoleta: descarta esa salida, recaptura una vez y solicita a visión una
decisión nueva con la escena actual. Este refresco no cuenta como una acción ejecutada. Si el contexto
vuelve a cambiar o la recaptura detecta contenido sensible, termina sin otro intento. El helper
compara el contexto al inicio y al final de cada captura para impedir que OCR o NVIDIA reciban una
imagen asociada a coordenadas obsoletas. Este digest vive solo durante el paso: no entra en memoria
persistente, auditoría o logs.

El scroll usa un evento propio ubicado en el centro de la porción visible de la ventana
Accessibility enfocada dentro del display seleccionado; nunca consulta, mueve o suplanta el cursor
nativo. Antes de publicarlo comprueba que el elemento bajo el punto todavía pertenece al mismo PID
y a la misma instancia. Si otra aplicación lo cubre o la geometría deja de ser válida, no desplaza
nada.

Un `AXPress`, desplazamiento o tecla local no se considera suficiente por sí solo. Jarvis recaptura
inmediatamente en el Mac. Si ya existe progreso, elimina la antigua espera fija de 450 ms. Solo
cuando esa primera captura sigue estable espera ese intervalo y realiza una captura final.
CoreGraphics reduce las imágenes a firmas dHash de 256 bits. Un cambio semántico Accessibility o al
menos 8 bits visuales cuenta como progreso; jitter de coordenadas, OCR, orden de ventanas y
variaciones menores se ignoran. Sin progreso suficiente, Jarvis informa estado incierto y no repite
ni consulta NVIDIA. Si cualquier captura revela un login, campo seguro o texto sensible, bloquea la
sesión antes de esperar, evaluar la firma o informar éxito.
Si cualquiera de las dos percepciones Accessibility está truncada, cambios entre sus subconjuntos no
cuentan como progreso: deben cambiar al menos 8 bits de la firma visual. Jarvis tampoco presenta
texto AX como evidencia nueva después de una acción ni infiere que un botón es único a partir de un
árbol incompleto. La sesión termina como incierta antes de declarar éxito con esa comparación.

Cuando NVIDIA debe elegir entre varios controles, su clic copia la etiqueta y el centro exactos de
un elemento `Accessibility` pulsable observado localmente. El daemon verifica esa relación y el
helper firmado comprueba otra vez la etiqueta del elemento real justo antes de `AXPress`. Si el
control cambió, desapareció o no coincide, Jarvis no pulsa nada y termina ese paso como estado
incierto. El puntero visual de Jarvis sigue siendo solo una representación; nunca mueve el cursor
nativo del usuario. Tras un clic correcto, el helper devuelve el identificador del display validado
y el retículo se dibuja en ese monitor. Una respuesta fallida o sin display válido no muestra un
puntero potencialmente engañoso.

El teclado, ratón y trackpad del usuario siempre tienen prioridad. Jarvis liga cada captura a la
última entrada física detectada por CoreGraphics y vuelve a comprobarla justo antes de cada efecto.
Si escribes, haces clic, scroll o mueves el puntero, termina la sesión sin recapturar ni reintentar y
responde que cedió el control. No existe un keylogger ni un monitor permanente: solo compara un
contador monotónico efímero dentro del helper firmado, sin guardar teclas, coordenadas o eventos y
sin enviarlos a NVIDIA.

La finalización remota también falla de forma cerrada. Para responder que el objetivo terminó,
NVIDIA debe copiar como evidencia el texto exacto de un elemento no sensible de Accessibility o el
título de una ventana incluidos en la observación local actual. Una frase inventada, aproximada o
detectada únicamente por OCR se rechaza como estado incierto; no activa el helper ni genera otra
captura o llamada al modelo.
Después de un clic, escritura, tecla o desplazamiento elegido por NVIDIA, la prueba debe ser además
nueva respecto a la observación anterior y el estado debe mostrar progreso perceptual. Jarvis envía
al especialista únicamente la lista de evidencias locales nuevas que puede citar. Un cambio de
píxeles sin una prueba Accessibility nueva, o una etiqueta que ya estaba visible antes de actuar,
no basta para declarar el objetivo terminado.
Cada acción elegida por NVIDIA provoca una recaptura inmediata, incluso si consume el último paso.
Jarvis bloquea en ese punto cualquier contenido sensible revelado y reutiliza la misma observación
en la decisión siguiente. Alcanzar el límite puede informar `step_limit`, pero no deja una acción
final sin inspección local ni genera dos capturas para el mismo estado.

El relay permanece inactivo en `computer.wait` hasta que exista una orden. Esa espera y el helper
firmado están ligados a la generación actual de la sesión macOS. Bloquear la pantalla invalida una
orden todavía en espera, termina el helper ya iniciado y suprime cualquier puntero tardío. Tras
desbloquear se crea una generación nueva; Jarvis no reintenta automáticamente el paso anterior.

## 11. Activación por «Jarvis»

La transcripción permanente no existe. Hasta que haya un modelo Core ML entrenado, la activación por
nombre permanece desactivada. Una vez habilitada, solo un clasificador acústico local inspecciona
buffers efímeros buscando «Jarvis»; Apple Speech, el daemon y NVIDIA permanecen inactivos hasta que
el nombre se confirma.

### Ruta recomendada desde la interfaz

1. Abre el icono de Jarvis en la Menu Bar.
2. Elige `Preparar activación por “Jarvis”…`.
3. Graba al menos 20 muestras de `jarvis` en condiciones variadas.
4. Graba al menos 20 muestras de `background`, incluyendo silencio y ruido habitual.
5. Cierra la ventana cuando ambas clases estén completas.
6. Desde el directorio del proyecto ejecuta:

```bash
./script/activate_wake_word.sh
```

7. Abre Jarvis y habilita explícitamente la escucha de la palabra de activación.

El flujo valida el dataset, entrena el clasificador local, instala el modelo y reinicia la app. Las
muestras permanecen privadas bajo:

```text
~/Library/Application Support/Aegis/WakeWordEnrollment
```

El modelo queda en:

```text
~/Library/Application Support/Aegis/Models/JarvisWakeWord.mlmodelc
```

El detector se pausa automáticamente durante captura y reproducción de voz, reposo del Mac,
presión térmica seria o indisponibilidad del daemon. En modo de bajo consumo permanece activo el
clasificador local mínimo para que «Jarvis» siga funcionando; Speech y NVIDIA continúan apagados.

### Dataset externo opcional

Debe contener exactamente las carpetas `jarvis/` y `background/`, con un mínimo de 20 clips reales
por clase:

```bash
./script/train_wake_word.sh /ruta/al/dataset
./script/activate_wake_word.sh /ruta/al/dataset
```

## 12. Identidad de hablantes

La identidad de voz sirve para seleccionar el perfil privado y para impedir que una voz no
reconocida enseñe preferencias al propietario; nunca autentica ni aprueba acciones. Jarvis exige
además presencia local reciente de la sesión macOS, descrita en la sección de conversación.

1. Abre Jarvis en la Menu Bar.
2. Pulsa el icono de dos personas.
3. Crea entre uno y ocho perfiles con identificadores simples, por ejemplo `guillermo` o
   `invitado`.
4. Graba al menos 20 clips por persona y 20 clips de fondo.
5. Pulsa `Entrenar modelo local`.
6. Si el modelo contiene varias voces, en `Contexto privado` selecciona al propietario y confirma.

La lista de propietario usa únicamente etiquetas del modelo compilado y validado. Si hay un solo
perfil, Jarvis lo utiliza automáticamente como candidato; todavía necesita presencia local reciente
para recibir contexto privado. Con varios perfiles y sin selección, todos los turnos de voz fallan
cerrados para historial, memoria y gustos privados. Cambiar o quitar el propietario rota
la conversación de voz actual, pero no borra datos. La selección guarda solo el identificador local
en `UserDefaults` junto con la huella SHA-256 del modelo; no guarda audio, embeddings o confianza.
Si reemplazas o reentrenas el modelo, Jarvis mostrará `Confirmar propietario`: confirma de nuevo
antes de usar historial o memoria privada, incluso si la etiqueta conserva el mismo nombre. Una
conversación vinculada a otra huella nunca se reutiliza. El comando de terminal `transcribe-submit`
no adivina al propietario de un modelo con varias voces.

La vía de terminal sirve para diagnóstico o recuperación:

```bash
./script/train_speaker_identity.sh --check
./script/activate_speaker_identity.sh
```

Datos y modelo se guardan respectivamente en:

```text
~/Library/Application Support/Aegis/SpeakerEnrollment
~/Library/Application Support/Aegis/Models/JarvisSpeakerIdentity.mlmodelc
```

No se envían muestras de entrenamiento a NVIDIA ni se incorporan a Git.

Después de decir «Jarvis», pregunta `¿Quién soy?`, `¿Me reconoces?` o `¿Sabes quién soy?`. Si la
evidencia de ese turno supera el umbral ya validado por el clasificador local, Jarvis responde con el
identificador del perfil. Si no, indica que no pudo reconocer la voz con suficiente confianza; no
adivina ni revela el porcentaje. La consulta conserva el preflight normal, pero no ejecuta otro
modelo ni envía el transcript mediante `voice.submit`. El resultado sirve para personalización y
nunca autentica al propietario, concede control, aprueba herramientas o reemplaza una confirmación.

## 13. Configuración de referencia

El nombre visible es Jarvis, pero se conserva el namespace técnico `aegis` para no invalidar
Keychain, permisos, memoria o servicios existentes.

### Modelos NVIDIA asignados

| Rol | Modelo principal | Respaldo |
| --- | --- | --- |
| Router, planificador y síntesis | `nvidia/nemotron-3.5-lightning-30b-a3b` | `nvidia/nemotron-3-nano-30b-a3b`; síntesis usa `openai/gpt-oss-20b` |
| Razonamiento crítico | `nvidia/nemotron-3-super-120b-a12b` | `openai/gpt-oss-120b` |
| Código y ciberseguridad | `deepseek-ai/deepseek-v4-pro-0813` | `deepseek-ai/deepseek-v4-flash-0731` |
| Visión y omni | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | `meta/muse-glimmer-30b` |
| Embeddings remotos | `nvidia/nemotron-3-embed-1b` | reservado para una fase API posterior; no recibe memoria actualmente |
| Voz | NVIDIA Magpie `Magpie-Multilingual.ES-US.Diego` | voz española estándar local de Apple |

Antes de esta tabla existe una ruta local adicional: `apple/system-language-model`, utilizada solo
por el planner para conversación breve sin herramientas. No reemplaza los especialistas NVIDIA ni
requiere una API key.

La disponibilidad de un modelo preview puede cambiar en NVIDIA. Los fallbacks se aplican solo en
las condiciones previstas por el cliente; valida los endpoints con los probes tras una
actualización.

El razonador crítico solo se añade a solicitudes de código o ciberseguridad clasificadas localmente
como riesgo alto o crítico. Trabaja en paralelo como revisor independiente y no recibe herramientas.
El sintetizador compara consenso e incertidumbres; nunca concede autoridad para ejecutar acciones.

NVIDIA recibe una carga minimizada: solicitud actual con redacción local de secretos e
identificadores básicos, sin memoria del dueño, historial de conversación ni identidad de voz. Los
resultados y autorizaciones de herramientas permanecen locales incluso si Apple Intelligence no
está disponible.

### Rutas locales

| Elemento | Ruta o identificador |
| --- | --- |
| Aplicación | `~/Applications/Jarvis.app` |
| Helper de cerebro local | `~/Applications/Jarvis.app/Contents/Helpers/jarvis-local-brain` |
| Helper de embeddings local | `~/Applications/Jarvis.app/Contents/Helpers/jarvis-local-embedding` |
| Memoria SQLite | `~/Library/Application Support/Aegis/memory.sqlite3` |
| Evaluaciones SQLite | `~/Library/Application Support/Aegis/evaluations.sqlite3` |
| Socket IPC | `~/Library/Application Support/Aegis/aegis.sock` |
| Auditoría | `~/Library/Application Support/Aegis/audit.jsonl` |
| Modelos locales | `~/Library/Application Support/Aegis/Models/` |
| Logs | `~/Library/Logs/Aegis/` |
| LaunchAgent daemon | `~/Library/LaunchAgents/ai.aegis.daemon.plist` |
| LaunchAgent app | `~/Library/LaunchAgents/ai.aegis.menubar.autostart.plist` |
| API NVIDIA en Keychain | servicio `ai.aegis.nvidia-nim`, cuenta `default` |
| Secreto IPC en Keychain | servicio `ai.aegis.ipc-auth`, cuenta `default` |

### Variables avanzadas

La configuración base no necesita un `.env`; de hecho, Jarvis no carga archivos `.env`. Los
valores seguros están definidos en `src/aegis_core/config.py`. Entre los overrides disponibles se
encuentran:

| Variable | Predeterminado | Efecto |
| --- | --- | --- |
| `AEGIS_MEMORY_RAG_NAMESPACE` | `user.default` | Namespace fijo de memoria del operador. |
| `AEGIS_MEMORY_RAG_LIMIT` | `5` | Cantidad máxima de recuerdos recuperados. |
| `AEGIS_MEMORY_EMBEDDING_BACKFILL_LIMIT` | `500` | Recuerdos recientes pendientes que se indexan en segundo plano al iniciar. |
| `AEGIS_EVALUATION_DATABASE_PATH` | `~/Library/Application Support/Aegis/evaluations.sqlite3` | Historial local sin contenido de usuario. |
| `AEGIS_EVALUATION_MAX_ENTRIES` | `10000` | Retención máxima de evaluaciones terminales. |
| `AEGIS_NVIDIA_TTS_VOICE` | `Magpie-Multilingual.ES-US.Diego` | Voz remota configurada. |
| `AEGIS_NVIDIA_TTS_LANGUAGE` | `es-US` | Idioma de la voz remota. |
| `AEGIS_NVIDIA_TTS_STREAM_URL` | endpoint Magpie `synthesize_online` | Endpoint HTTPS de audio PCM incremental. |
| `AEGIS_MAX_CONCURRENCY` | `4` | Concurrencia máxima hacia el proveedor. |
| `AEGIS_MAX_OUTPUT_TOKENS` | `4096` | Límite máximo de salida. |
| `AEGIS_JOB_TIMEOUT_SECONDS` | `120` | Tiempo máximo de un job. |
| `AEGIS_LOCAL_BRAIN_EXECUTABLE_PATH` | helper dentro de `Jarvis.app` | Ruta firmada del cerebro local. |
| `AEGIS_LOCAL_BRAIN_TIMEOUT_SECONDS` | `20` | Presupuesto total del turno local; el primer fragmento conserva un límite interno de 4 segundos. |
| `AEGIS_LOCAL_EMBEDDING_EXECUTABLE_PATH` | helper dentro de `Jarvis.app` | Ruta del embedding on-device. |
| `AEGIS_LOCAL_EMBEDDING_TIMEOUT_SECONDS` | `5` | Presupuesto máximo de un lote local. |
| `AEGIS_AUDIT_MAX_BYTES` | `16777216` | Capacidad máxima del log de auditoría. |

Los overrides exportados en una terminal solo afectan procesos iniciados desde esa terminal. El
LaunchAgent usa los valores versionados del proyecto y fija automáticamente
`AEGIS_WORKSPACE_ROOT`. Para cambiar de forma persistente un límite del servicio, modifica la
configuración como un cambio de código revisado, ejecuta las pruebas y reinstala el daemon; no
guardes secretos en variables, `.zshrc`, plist o `.env`.

La memoria semántica usa exclusivamente el helper local. No existe un override que envíe el texto
indexado o las consultas al endpoint de embeddings NVIDIA; FTS5 permanece activo en paralelo.

## 14. Actualizar Jarvis

Antes de actualizar, conserva cualquier cambio propio y comprueba el estado:

```bash
git status --short
git pull --ff-only
/opt/homebrew/bin/uv sync --all-groups --no-editable \
  --python /opt/homebrew/bin/python3.11 \
  --cache-dir .uv-cache
/opt/homebrew/bin/uv run --no-sync pytest
./script/test_native.sh
./script/daemon_service.sh install
./script/menu_bar_service.sh install
```

No borres ni recrees la identidad `Jarvis Local Development`. Al reutilizarla, macOS conserva la
atribución estable de permisos entre builds.

## 15. Operación y diagnóstico

### Estado de servicios

```bash
./script/daemon_service.sh status
./script/menu_bar_service.sh status
./script/aegis.sh daemon-status
```

### Reiniciar

Los instaladores son idempotentes y constituyen el reinicio seguro:

```bash
./script/daemon_service.sh install
./script/menu_bar_service.sh install
```

### Logs

```bash
tail -n 100 "$HOME/Library/Logs/Aegis/daemon.error.log"
tail -n 100 "$HOME/Library/Logs/Aegis/menu-bar.error.log"
/usr/bin/log show --last 10m --style compact \
  --predicate 'subsystem == "ai.aegis.menubar"'
```

Los logs no deben contener claves, audio, transcripciones, respuestas, capturas ni argumentos
privados.

### Integridad de auditoría

```bash
./script/aegis.sh verify-audit \
  "$HOME/Library/Application Support/Aegis/audit.jsonl"
```

Resultado esperado: `status=ok`. Si aparece `audit_integrity_failure`, Jarvis se bloquea de forma
deliberada. Conserva el archivo para análisis y no intentes repararlo mientras el daemon esté vivo.

### Prueba de estabilidad del daemon

```bash
./script/aegis.sh daemon-soak
```

Esta prueba usa IPC local, no NVIDIA. Comprueba latencia, memoria, arquitectura, integridad y PID.

### Autoevaluación de la sesión

```bash
./script/aegis.sh self-evaluation
```

Devuelve JSON con cantidad de trabajos terminales, tasa de éxito, latencias p50/p95 y distribución
entre cerebro local, NVIDIA y rutas deterministas. Cada trabajo mide además tiempo al primer
fragmento, modelo, cantidad de fragmentos y herramienta. Para respuestas sin herramientas, un
evaluador local registra modo conversacional, puntuación, longitud y banderas acotadas; nunca guarda
el texto evaluado. Esta evaluación es automática y no persiste prompts, respuestas ni argumentos
de herramientas. Solo conserva campos operativos acotados en `evaluations.sqlite3`, un archivo
local privado que se valida antes de cada acceso.

El objeto `quality` interpreta la muestra actual:

- `insufficient_data`: todavía no hay 20 trabajos terminales;
- `competitive`: éxito mínimo de 95 %, primer fragmento p95 de hasta 2 segundos, conversación
  completa p95 de hasta 8 segundos y calidad conversacional aprobada en al menos 95 % de las
  respuestas evaluadas;
- `needs_attention`: existe una muestra suficiente, pero al menos un objetivo no se cumple.

`observed.action_success_rate` separa la fiabilidad de herramientas de la conversación y solo
cuenta resultados con `outcome_verified=true`. En control visual esto exige una captura posterior
que pruebe el objetivo; un bloqueo seguro o el límite de pasos puede terminar de forma controlada,
pero no cuenta como acción correcta. Un valor `null` significa que la sesión todavía no ejecutó
acciones; no es un fallo. Reiniciar el daemon conserva la muestra hasta el límite de retención
configurado; las entradas más antiguas se eliminan automáticamente.

`observed.owner_recognition_rate` aparece cuando la sesión contiene voz. El objetivo es 90 % y
cuenta únicamente la coincidencia local con el único perfil configurado. Jarvis no publica el
nombre, identificador, confianza ni audio. Esta métrica sirve para detectar que el perfil necesita
reentrenamiento; no sustituye las confirmaciones de acciones.

`observed.response_quality_pass_rate` y `response_quality_score_p50` miden la disciplina de la
respuesta sin otra inferencia. `response_quality_flags` cuenta aperturas prefabricadas, eco literal
de la solicitud, exceso de longitud, frases repetidas, afirmaciones de identidad humana y lenguaje
de dependencia relacional. Son señales conservadoras de regresión, no un diagnóstico emocional ni
una autorización para reescribir respuestas. Un registro antiguo sin estos campos continúa siendo
válido y queda fuera del denominador hasta que existan respuestas nuevas evaluadas.

`observed.owner_feedback_helpful_rate` agrega las calificaciones explícitas del propietario. Se
publica desde la primera muestra, pero no afecta `competitive` hasta reunir cinco respuestas
calificadas; desde entonces el objetivo mínimo es 80 %. `owner_feedback_count` cuenta respuestas
calificadas y `feedback_jobs` cuenta los acuses locales, que no forman parte de `conversation_jobs`.

`observed.repair_recovery_rate` mide cuántas reparaciones calificadas terminaron como `helpful`.
`repair_attempts` cuenta los turnos que consumieron una ventana y `repair_rated_count` solo aquellos
que el propietario calificó después. La compuerta permanece en `null` hasta reunir tres
reparaciones calificadas; desde entonces exige una recuperación mínima de 80 %. Reiniciar el daemon
conserva esta métrica, pero no reconstruye ni mantiene abierta una ventana de reparación.

## 16. Skills: especialización y aprendizaje seguro

Jarvis incluye estas Skills listas para usar; se activan automáticamente por intención y no hay que
habilitarlas en la interfaz:

- `mac-control-expert`: decide entre una acción nativa y control visual de Safari, Chrome, Firefox
  u otra aplicación explícita.
- `browser-navigation-expert`: diferencia investigar, leer una URL, abrirla o interactuar con una
  página.
- `security-audit-expert`: guía auditorías defensivas de macOS, red y código.
- `code-review-expert`: busca fallos demostrables y recomienda cambios mínimos.
- `personal-productivity-expert`: especializa correo, calendario, contactos y recordatorios.

Una Skill no es un programa ni un permiso. Solo contiene disparadores, instrucciones, un rol y una
lista de herramientas que ya existen. El broker vuelve a validar cada llamada y conserva todas las
confirmaciones actuales.

### Ver las Skills disponibles

```bash
./script/aegis.sh skills-list
```

Cada línea JSON indica identificador, nombre, origen, rol y herramientas. La última línea debe
mostrar `status=ok`.

### Enseñar una Skill propia

1. Copia `examples/skills/research-first.json` fuera del repositorio si incluirás preferencias
   privadas.
2. Cambia `skill_id`, nombre, descripción, frases, términos e instrucciones.
3. Conserva únicamente nombres de herramientas que el rol ya puede usar.
4. Valida e instala el archivo:

```bash
./script/aegis.sh skills-learn /ruta/privada/mi-skill.json
./script/aegis.sh skills-list
```

No hay que reiniciar: el daemon detecta el cambio cuando llega la siguiente solicitud. El archivo
validado queda en `~/Library/Application Support/Aegis/skills` con permisos `0600`. Las
instrucciones aprendidas permanecen en el Mac y nunca entran en la carga NVIDIA. Si la Skill ofrece
una herramienta, NVIDIA puede ver solamente la solicitud actual redactada y el esquema de esa
herramienta ya autorizada por el rol; no recibe el manifiesto privado.

El aprendizaje rechaza campos extra, identificadores inválidos, credenciales probables, texto para
omitir políticas, herramientas fuera del rol, código arbitrario y archivos demasiado grandes. Una
Skill puede orientar o reducir herramientas, pero no conceder Screen Recording, Accessibility,
Mail, Calendar, red, shell ni ningún permiso.

Ejemplo de invocación después de instalar la plantilla:

```text
Jarvis, activa modo investigación prioritaria y revisa las novedades públicas de NVIDIA NIM.
```

El MVP conserva una sola acción de herramienta por turno. Esto evita cadenas implícitas y permite
revisar cada mutación de forma exacta.

### Actualizar o retirar una Skill

Instalar otra vez el mismo `skill_id` reemplaza atómicamente solo esa Skill aprendida. Para
retirarla:

```bash
./script/aegis.sh skills-forget research-first
```

Las Skills integradas forman parte del núcleo firmado y no se pueden reemplazar o borrar mediante
el almacén aprendido.

## 17. Solución de problemas

### `doctor` indica `nvidia_api_key=missing`

1. Genera o copia una clave `nvapi-…` válida.
2. Ejecuta `./script/aegis.sh import-nvidia-key`.
3. Ejecuta `./script/aegis.sh doctor` y `./script/aegis.sh probe-nvidia`.

No uses `export NVIDIA_API_KEY=…` como solución permanente: puede terminar en historial, logs o
configuración de shell y no es necesaria con Keychain.

### `provider=missing` o NVIDIA aparece inactivo

```bash
./script/aegis.sh probe-nvidia
./script/aegis.sh probe-nvidia-swarm
./script/aegis.sh probe-nvidia-tools
./script/daemon_service.sh install
./script/aegis.sh daemon-status
```

Si la entrada existe pero el probe falla, revisa conexión, cuota, vigencia y disponibilidad del
endpoint en NVIDIA. `probe-nvidia-swarm` verifica cada primario con contenido sintético y falla si
un rol continúa en fallback después de un reintento. `probe-nvidia-tools` valida function calling
sin autorizar ni ejecutar la herramienta. `configured` por sí solo no garantiza que la clave siga
activa.

Si `local_model=available`, la conversación breve todavía puede funcionar sin NVIDIA. Visión,
herramientas y razonamiento profundo requieren que el proveedor remoto vuelva a estar disponible.

### `local_model=unavailable`

En macOS 14 o 15 es el comportamiento esperado. En macOS 26, confirma que Apple Intelligence esté
activado y listo, reinstala la app y reinicia el daemon:

```bash
./script/menu_bar_service.sh install
./script/daemon_service.sh install
./script/aegis.sh daemon-status
```

No descargues un modelo ni modifiques permisos del helper manualmente. Jarvis conmuta a NVIDIA de
forma automática mientras la ruta local no esté disponible.
Si el proceso local inicia pero no produce ningún fragmento en cuatro segundos, Jarvis lo termina y
conmuta a NVIDIA. Después de un fragmento parcial no mezcla proveedores ni repite la respuesta.

La voz hablada también tiene fallback independiente: si NVIDIA TTS no inicia en 1,8 segundos, el
turno continúa con la voz local y mantiene ese mismo timbre hasta terminar. El siguiente turno
vuelve a intentar la voz NVIDIA. Si el stream se corta después de comenzar, Jarvis termina los
bloques ya programados, no repite lo dicho y usa la voz local solo para los segmentos siguientes.

### `service_not_loaded` o `app_not_running`

```bash
./script/daemon_service.sh install
./script/menu_bar_service.sh install
```

Si persiste, revisa los dos archivos `*.error.log` de la sección anterior.

### El control sigue sin aparecer activo

1. Ejecuta `./script/local_codesign_identity.sh status`.
2. Ejecuta `./script/menu_bar_service.sh computer-permissions`.
3. En Privacidad y seguridad activa pantalla para `Jarvis` y Control para `Jarvis` y el helper si
   aparece.
4. Cierra Ajustes del Sistema.
5. Espera el reinicio automático de Jarvis y abre su panel; `CONTROL` debe mostrar `LISTO`.

Si sigue sin aplicarse, ejecuta de nuevo `./script/menu_bar_service.sh computer-permissions`: el
comando solicita únicamente el permiso que todavía falta, sin revocar los que ya están activos.

Si existen entradas antiguas o duplicadas de Jarvis, desactiva las obsoletas, conserva la que apunta
a `~/Applications/Jarvis.app` y reinstala la app con el script. No firmes el bundle con una identidad
diferente.

### Micrófono o voz aparecen denegados

Abre sus paneles de Privacidad y seguridad, activa Jarvis y ejecuta:

```bash
./script/menu_bar_service.sh permissions
```

### No aparece el notch

El panel ambiental solo se crea en la pantalla integrada cuando macOS reporta una geometría de
notch válida. No se muestra en monitores sin notch. Comprueba que Jarvis esté corriendo con
`./script/menu_bar_service.sh status`.

### «Jarvis» no activa la escucha

Comprueba que:

- el modelo se entrenó y validó;
- la escucha se habilitó explícitamente en Menu Bar;
- micrófono y daemon están disponibles;
- el Mac no está bajo presión térmica seria, en reposo o reproduciendo voz. Bajo consumo conserva
  únicamente el detector local del nombre.

Valida el modelo instalado:

```bash
./script/train_wake_word.sh --validate-model \
  "$HOME/Applications/Jarvis.app/Contents/Resources/JarvisWakeWord.mlmodelc"
```

### La compilación pierde permisos después de cada cambio

```bash
./script/local_codesign_identity.sh install
./script/menu_bar_service.sh install
```

Trabaja siempre con `~/Applications/Jarvis.app`; `/private/tmp/Jarvis.app` es solo un bundle de
compilación y prueba.

## 18. Detener o desinstalar

### Detener el autoinicio sin borrar datos

```bash
./script/menu_bar_service.sh uninstall
./script/daemon_service.sh uninstall
```

Esto cierra los procesos y elimina los LaunchAgents. Conserva `~/Applications/Jarvis.app`, Keychain,
memoria, modelos, auditoría y logs. Es la opción segura y reversible.

### Retirar la API NVIDIA

Solo si realmente deseas revocarla en este Mac, detén primero los servicios y ejecuta:

```bash
/usr/bin/security delete-generic-password \
  -a default \
  -s ai.aegis.nvidia-nim
```

La eliminación local no revoca la clave en NVIDIA. Revócala también desde el portal NVIDIA. Esta
acción no se puede deshacer salvo importando otra vez una copia válida.

No elimines `ai.aegis.ipc-auth` durante una instalación activa: es el secreto local que autentica
la comunicación entre la app y el daemon.

Los datos persistentes, modelos y bundle se eliminan manualmente desde Finder solo cuando exista un
respaldo y se haya decidido un borrado completo. No forman parte de `uninstall` para evitar pérdida
accidental.

## 19. Lista de seguridad

- Nunca guardar una clave `nvapi-…` en Git, `.env`, `.zshrc`, plist, logs o documentación.
- Nunca compartir capturas donde aparezca la API.
- Usar `import-nvidia-key` para que el portapapeles se limpie automáticamente.
- Mantener la identidad de firma estable para preservar y atribuir correctamente TCC.
- Aprobar herramientas solo después de revisar el objetivo y los argumentos.
- Recordar que pantalla e imágenes aprobadas se envían a NVIDIA para inferencia.
- Verificar que la memoria semántica indique `available`; FTS5 debe seguir operativo si no lo está.
- Revisar una Skill aprendida antes de instalarla y no incluir credenciales o datos innecesarios.
- Ejecutar pruebas antes de reinstalar una actualización.
- Conservar memoria, modelos y auditoría fuera del repositorio.

Cuando todos los puntos de la sección 9 se cumplen, Jarvis queda instalado, configurado y listo para
operación voice-first en segundo plano.
