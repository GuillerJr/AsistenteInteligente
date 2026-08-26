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

`doctor` comprueba arquitectura, configuración y presencia de la clave. Los `probe-*` realizan
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

La compilación incluye `jarvis-local-brain` dentro de `Jarvis.app/Contents/Helpers`. El daemon solo
lo ejecuta si es un archivo real, ejecutable, propiedad del usuario y sin escritura para grupo u
otros. Su ausencia no bloquea la instalación: NVIDIA queda como cerebro disponible.

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

Mail y Calendario pueden mostrar una solicitud adicional de Automatización la primera vez que una
acción aprobada los necesite. macOS conserva esa decisión. Jarvis nunca lee el cuerpo de los
correos y las acciones que modifican datos exigen una confirmación de un solo uso.

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

La respuesta comienza mientras el modelo todavía genera. Jarvis separa el stream únicamente en
frases completas para evitar palabras cortadas y nunca vuelve a pronunciar un fragmento ya emitido.
Si dices «Jarvis» mientras está procesando o hablando, cancela el trabajo y el audio actuales y abre
un turno nuevo. No es necesario esperar al final de la respuesta.

### Qué cerebro atiende cada solicitud

- Apple on-device: conversación breve, solo texto, sin herramientas y dentro del límite local.
- NVIDIA NIM: código, ciberseguridad, razonamiento profundo, visión, contexto largo y herramientas.
- Fallback: si Apple Intelligence no está disponible o el helper falla, el mismo turno pasa a
  NVIDIA. Una ruta local nunca ejecuta herramientas.

Una mención aislada de «hoy», «app», «clima», «precio» o «noticias» no carga herramientas. Jarvis
busca una orden explícita como «abre Safari», «revisa mi correo», «crea un evento», «busca…» o una
pregunta de información actual. Esto evita latencia remota durante una conversación casual, pero
no autoriza acciones: toda herramienta conserva su política y, cuando corresponde, la aprobación
de un solo uso.

Las siguientes órdenes inequívocas no necesitan una inferencia para preparar la aprobación:

- `Abre Safari` — también admite Chrome, Firefox, Calendario, Mail, Notas, Vista Previa, Xcode y
  Spotify mediante una lista local de bundle IDs conocidos.
- `Ejecuta el atajo Informe diario` — conserva literalmente el nombre y no admite rutas ni entrada.
- `Revisa el estado de Git`, `Lista los procesos`, `Lista los puertos abiertos` y
  `Revisa la postura de seguridad` — seleccionan uno de los cuatro diagnósticos fijos existentes.

Jarvis muestra la aprobación pendiente igual que en el camino NVIDIA. No ejecuta nada antes de
`Aprobar una vez`. Si la frase contiene más de una acción, una negación, una aplicación desconocida,
un adjunto o audio no transcrito localmente, el atajo determinista no se usa.

Para las órdenes que sí requieren NVIDIA, Jarvis no envía el catálogo completo. Una solicitud como
`Revisa mi correo` expone solamente `mail_list_recent`; `Crea un evento` solo
`calendar_create_event`; `Escanea mi red` solo `network_discover_hosts`; y `Controla Safari` solo
`computer_use`. Esto reduce tokens y evita que el modelo elija una capacidad ajena a la intención.
El broker verifica de nuevo el conjunto ofrecido y responde `tool_not_offered` ante cualquier
desviación. Si la intención operativa es ambigua, conserva el catálogo permitido del rol y todas las
políticas habituales.

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

La identidad de voz sirve para personalización y para impedir que una voz no reconocida enseñe
preferencias al perfil del propietario; nunca autentica ni aprueba acciones.

1. Abre Jarvis en la Menu Bar.
2. Pulsa el icono de dos personas.
3. Crea entre uno y ocho perfiles con identificadores simples, por ejemplo `guillermo` o
   `invitado`.
4. Graba al menos 20 clips por persona y 20 clips de fondo.
5. Pulsa `Entrenar modelo local`.

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

## 13. Configuración de referencia

El nombre visible es Jarvis, pero se conserva el namespace técnico `aegis` para no invalidar
Keychain, permisos, memoria o servicios existentes.

### Modelos NVIDIA asignados

| Rol | Modelo principal | Respaldo |
| --- | --- | --- |
| Router y síntesis | `nvidia/nemotron-3.5-lightning-30b-a3b` | `nvidia/nemotron-3-nano-30b-a3b` o `openai/gpt-oss-20b`, según el rol |
| Planificador | `openai/gpt-oss-20b` | `deepseek-ai/deepseek-v4-flash-0731` |
| Razonamiento crítico | `deepseek-ai/deepseek-v4-flash-0731` | `minimaxai/minimax-m3` |
| Código y ciberseguridad | `deepseek-ai/deepseek-v4-flash-0731` | `openai/gpt-oss-20b` |
| Visión y omni | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` | `meta/muse-glimmer-30b` |
| Embeddings | `nvidia/nemotron-3-embed-1b` | recuperación FTS5 local |
| Voz | NVIDIA Magpie `Magpie-Multilingual.ES-US.Diego` | voz española estándar local de Apple |

Antes de esta tabla existe una ruta local adicional: `apple/system-language-model`, utilizada solo
por el planner para conversación breve sin herramientas. No reemplaza los especialistas NVIDIA ni
requiere una API key.

La disponibilidad de un modelo preview puede cambiar en NVIDIA. Los fallbacks se aplican solo en
las condiciones previstas por el cliente; valida los endpoints con los probes tras una
actualización.

### Rutas locales

| Elemento | Ruta o identificador |
| --- | --- |
| Aplicación | `~/Applications/Jarvis.app` |
| Helper de cerebro local | `~/Applications/Jarvis.app/Contents/Helpers/jarvis-local-brain` |
| Memoria SQLite | `~/Library/Application Support/Aegis/memory.sqlite3` |
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
| `AEGIS_MEMORY_REMOTE_EMBEDDINGS_ENABLED` | `false` | Si es `true`, envía texto de memoria y consultas al endpoint de embeddings NVIDIA. |
| `AEGIS_MEMORY_RAG_NAMESPACE` | `user.default` | Namespace fijo de memoria del operador. |
| `AEGIS_MEMORY_RAG_LIMIT` | `5` | Cantidad máxima de recuerdos recuperados. |
| `AEGIS_NVIDIA_TTS_VOICE` | `Magpie-Multilingual.ES-US.Diego` | Voz remota configurada. |
| `AEGIS_NVIDIA_TTS_LANGUAGE` | `es-US` | Idioma de la voz remota. |
| `AEGIS_MAX_CONCURRENCY` | `4` | Concurrencia máxima hacia el proveedor. |
| `AEGIS_MAX_OUTPUT_TOKENS` | `4096` | Límite máximo de salida. |
| `AEGIS_JOB_TIMEOUT_SECONDS` | `120` | Tiempo máximo de un job. |
| `AEGIS_LOCAL_BRAIN_EXECUTABLE_PATH` | helper dentro de `Jarvis.app` | Ruta firmada del cerebro local. |
| `AEGIS_LOCAL_BRAIN_TIMEOUT_SECONDS` | `20` | Presupuesto máximo del turno local. |
| `AEGIS_AUDIT_MAX_BYTES` | `16777216` | Capacidad máxima del log de auditoría. |

Los overrides exportados en una terminal solo afectan procesos iniciados desde esa terminal. El
LaunchAgent usa los valores versionados del proyecto y fija automáticamente
`AEGIS_WORKSPACE_ROOT`. Para cambiar de forma persistente un límite del servicio, modifica la
configuración como un cambio de código revisado, ejecuta las pruebas y reinstala el daemon; no
guardes secretos en variables, `.zshrc`, plist o `.env`.

Los embeddings remotos están desactivados por privacidad. Activarlos significa aceptar que el
contenido indexado y cada consulta abandonen el Mac. FTS5 local funciona sin esa opción.

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
fragmento, modelo, cantidad de fragmentos y herramienta. Esta evaluación es automática y no
persiste prompts, respuestas ni argumentos de herramientas.

El objeto `quality` interpreta la muestra actual:

- `insufficient_data`: todavía no hay 20 trabajos terminales;
- `competitive`: éxito mínimo de 95 %, primer fragmento p95 de hasta 2 segundos y conversación
  completa p95 de hasta 8 segundos;
- `needs_attention`: existe una muestra suficiente, pero al menos un objetivo no se cumple.

`observed.action_success_rate` separa la fiabilidad de herramientas de la conversación y solo
cuenta resultados con `outcome_verified=true`. En control visual esto exige una captura posterior
que pruebe el objetivo; un bloqueo seguro o el límite de pasos puede terminar de forma controlada,
pero no cuenta como acción correcta. Un valor `null` significa que la sesión todavía no ejecutó
acciones; no es un fallo. Reiniciar el daemon reinicia también esta muestra porque la telemetría no
se persiste.

`observed.owner_recognition_rate` aparece cuando la sesión contiene voz. El objetivo es 90 % y
cuenta únicamente la coincidencia local con el único perfil configurado. Jarvis no publica el
nombre, identificador, confianza ni audio. Esta métrica sirve para detectar que el perfil necesita
reentrenamiento; no sustituye las confirmaciones de acciones.

## 16. Solución de problemas

### `doctor` indica `nvidia_api_key=missing`

1. Genera o copia una clave `nvapi-…` válida.
2. Ejecuta `./script/aegis.sh import-nvidia-key`.
3. Ejecuta `./script/aegis.sh doctor` y `./script/aegis.sh probe-nvidia`.

No uses `export NVIDIA_API_KEY=…` como solución permanente: puede terminar en historial, logs o
configuración de shell y no es necesaria con Keychain.

### `provider=missing` o NVIDIA aparece inactivo

```bash
./script/aegis.sh probe-nvidia
./script/daemon_service.sh install
./script/aegis.sh daemon-status
```

Si la entrada existe pero el probe falla, revisa conexión, cuota, vigencia y disponibilidad del
endpoint en NVIDIA. `configured` por sí solo no garantiza que la clave siga activa.

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

La voz hablada también tiene fallback independiente: si NVIDIA TTS no inicia en 1,8 segundos, el
turno continúa con la voz local y mantiene ese mismo timbre hasta terminar. El siguiente turno
vuelve a intentar la voz NVIDIA.

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

## 17. Detener o desinstalar

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

## 18. Lista de seguridad

- Nunca guardar una clave `nvapi-…` en Git, `.env`, `.zshrc`, plist, logs o documentación.
- Nunca compartir capturas donde aparezca la API.
- Usar `import-nvidia-key` para que el portapapeles se limpie automáticamente.
- Mantener la identidad de firma estable para preservar y atribuir correctamente TCC.
- Aprobar herramientas solo después de revisar el objetivo y los argumentos.
- Recordar que pantalla e imágenes aprobadas se envían a NVIDIA para inferencia.
- Mantener desactivados los embeddings remotos salvo consentimiento explícito.
- Ejecutar pruebas antes de reinstalar una actualización.
- Conservar memoria, modelos y auditoría fuera del repositorio.

Cuando todos los puntos de la sección 9 se cumplen, Jarvis queda instalado, configurado y listo para
operación voice-first en segundo plano.
