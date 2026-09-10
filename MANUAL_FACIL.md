# Jarvis: manual fácil de instalación y arquitectura

Este documento está pensado para dos tipos de lector: quien solo quiere poner Jarvis a funcionar y
quien necesita entender por qué está construido de esta manera. Si una comprobación falla, consulta
el manual técnico completo en `docs/MANUAL_INSTALACION_CONFIGURACION.md`; no desactives una medida de
seguridad para “hacer que funcione”.

## 1. ¿Qué es Jarvis y por qué es diferente?

Jarvis no es simplemente una ventana bonita conectada a una API de pago. Es un agente local que
vive en tu Mac: escucha una activación explícita, comprende el estado permitido del equipo, conserva
memoria cifrada y ejecuta herramientas bajo reglas de seguridad. Su nombre visible es **Jarvis**;
internamente usa el namespace técnico **`aegis`** para no romper rutas, servicios ni llaves antiguas.

Imagina una **caja fuerte de privacidad** construida dentro del procesador Apple. En modo local, los
buffers de voz, las muestras biométricas y las capturas usadas por Vision se procesan en memoria y no
se guardan como fotografías o grabaciones permanentes. La comunicación entre la app y el daemon no
sale del Mac: viaja por `aegis.sock`, una tubería Unix autenticada con HMAC-SHA256.

Hay que distinguir las políticas activas:

- **Modo local:** Apple Foundation Models, MLX, Vision, Speech, SoundAnalysis y SQLite trabajan en el
  Mac. No envía prompts, audio ni imágenes a un proveedor de IA. El coste por uso del modelo es
  **USD 0**, aparte de la electricidad y el propio equipo.
- **Modo híbrido opcional:** Jarvis puede consultar NVIDIA NIM para especialistas remotos si el dueño
  lo configura. Esa operación sí usa Internet y queda sujeta a disponibilidad, cuota y condiciones
  del proveedor. La biometría y las credenciales siguen siendo locales, pero no debe describirse este
  modo como totalmente offline ni garantizarse un coste externo de cero.
- **CLI de ingeniería — NVIDIA por defecto:** `jarvis` usa `nvidia_only`, sin cargar otro modelo
  local ni sustituirlo silenciosamente por Apple/MLX si falla la conexión. Las consultas, el historial
  de esa sesión y los fragmentos de código leídos se procesan en NVIDIA. Requiere la clave del
  Llavero, Internet y cuota. El audio y la biometría no se envían por esta ruta. Las instrucciones
  completas están en [la guía del CLI](docs/ENGINEERING_CLI.md).

Esta separación es importante: una promesa de privacidad solo vale si explica exactamente cuál ruta
está activa.

## 2. ¿Cómo funciona por dentro? La analogía del cuerpo humano

### El cuerpo y los sentidos: Swift y macOS

La aplicación nativa es como los ojos y los oídos. `AVAudioEngine` recibe pequeños fragmentos de
audio; Speech y SoundAnalysis reconocen palabras y la identidad acústica local. ScreenCaptureKit
obtiene únicamente la región autorizada de pantalla y Vision realiza OCR para convertir píxeles en
texto y coordenadas. Los frames transitorios se procesan en memoria, sin crear capturas en el SSD.

El sistema vigila temperatura y consumo. Si un MacBook Air sin ventilador entra en presión térmica,
los sensores no esenciales se pausan antes de perjudicar la conversación o calentar el equipo.

### El cerebro silencioso: el daemon Python

Python mantiene el orquestador, las políticas, la memoria GraphRAG cifrada y los trabajos en segundo
plano. La app Swift y el daemon conversan mediante `aegis.sock`. Cada mensaje lleva una firma HMAC:
es como una pulsera imposible de falsificar que permite al daemon distinguir a Jarvis de otro proceso
local.

Las órdenes deterministas —consultar la hora, volumen, estado o una aplicación— evitan usar un modelo
cuando una función nativa es más rápida y exacta. Las solicitudes abiertas pueden usar Apple
Intelligence o MLX local. Solo si el modo híbrido está habilitado y la política lo permite se recurre
a un especialista remoto para razonamiento, código o visión.

### El efector invisible: SilentExecutor

En lugar de apoderarse del ratón que estás utilizando, Jarvis intenta primero el árbol de
Accesibilidad de macOS. Si un control no ofrece una acción accesible, puede enviar un evento lógico al
PID de la aplicación autorizada. Antes y después compara una huella SHA-256 del estado visible; si la
pantalla no cambió, no finge que la acción tuvo éxito y activa su ruta de recuperación.

Esto permite trabajar en segundo plano en muchas aplicaciones. No es una garantía universal: algunas
apps ignoran eventos dirigidos, protegen campos sensibles o exigen foco por diseño. En esos casos
Jarvis falla de forma cerrada o pide intervención en lugar de secuestrar el cursor.

## 3. Instalación en 3 pasos

Necesitas un Mac Apple Silicon compatible, las herramientas de desarrollo de Apple instaladas y una
copia local del proyecto. Los permisos de macOS siempre los concede una persona; ningún script debe
saltarse TCC.

### Paso 1: abre Terminal y entra en la carpeta

Abre **Aplicaciones → Utilidades → Terminal**. Después escribe `cd`, un espacio, arrastra la carpeta
del proyecto sobre la ventana de Terminal y pulsa Intro. El resultado se parecerá a:

```bash
cd "$HOME/Documents/Proyectos/AsistenteInteligente"
```

### Paso 2: compila y verifica

Ejecuta:

```bash
./script/build_and_run.sh --verify
```

Este comando compila la app y sus helpers, crea un bundle temporal y verifica la firma local. No
modifica macOS ni concede permisos en tu nombre. Si termina con error, detente y lee el mensaje.

### Paso 3: instala Jarvis y activa el guardián de Git

Para instalar la app y el daemon del usuario:

```bash
./script/menu_bar_service.sh install
./script/daemon_service.sh install
```

Después activa el guardián del repositorio:

```bash
./script/setup_git_gates.sh --install
./script/setup_git_gates.sh --check
```

Este guardián es un portero invisible con tres revisiones: antes de cada commit comprueba el código
rápidamente; antes de subirlo ejecuta la prueba completa; y deja la prueba de micrófono, permisos y
hardware para un comando explícito. Así un permiso temporal de macOS no bloquea una corrección de
código. La comprobación correcta muestra:

```text
status=active hooks_path=.githooks pre_commit=fast pre_push=full hardware=manual
```

La revisión física se ejecuta cuando la app y el daemon están encendidos:

```bash
./script/macos_qualification_gate.sh
```

Para entregar Jarvis a otro Mac existe una cadena más estricta. P12 comprueba firma y notarización;
P13 comprueba algo diferente: que el ZIP lleve también el “cerebro” Python y no dependa de la carpeta
del programador. El comando comercial es:

```bash
AEGIS_CODESIGN_IDENTITY="Developer ID Application: Nombre (TEAMID)" \
AEGIS_NOTARY_PROFILE="jarvis-notary" \
./script/p13_self_contained_runtime_gate.sh
```

Esta compuerta necesita primero la prueba física final de voz y las credenciales de distribución de
Apple. Los tests del código pueden completarse sin voz, pero la certificación comercial no se finge.

P14 añade el “cinturón de seguridad” de las actualizaciones. Jarvis comprueba una firma matemática
Ed25519 antes de aceptar un ZIP, bloquea versiones antiguas y guarda temporalmente la app anterior.
Si la nueva versión no arranca sana, recupera la anterior. La llave privada del publicador permanece
en el Llavero de macOS y nunca se incluye en Git ni en la aplicación. La preparación se hace una vez:

```bash
.venv/bin/python script/update_channel.py init
```

La comprobación comercial completa se ejecuta con `./script/p14_secure_update_gate.sh`; requiere las
mismas credenciales Apple y la prueba física final que P13. Los detalles para verificar, instalar o
recuperar una actualización están en
[`docs/quality/P14_SECURE_UPDATE_QUALIFICATION.md`](docs/quality/P14_SECURE_UPDATE_QUALIFICATION.md).

En el primer inicio, abre **Ajustes del Sistema → Privacidad y seguridad** y concede solo los permisos
que vayas a utilizar: Micrófono, Reconocimiento de voz, Grabación de pantalla y Accesibilidad. El
manual técnico explica el orden exacto y cómo diagnosticar cada estado. NVIDIA no es necesario para
el modo local; su credencial se configura por separado y nunca debe guardarse en el repositorio.

## 4. Márgenes de mejora estratégicos

### Cerebro multi-modelo completamente offline

El objetivo de mayor impacto es trasladar planificación compleja, código y visión residual a modelos
locales pequeños, cuantizados y especializados. El reto no es solo “cargar otro modelo”: hay que medir
calidad, memoria unificada, primer token y temperatura sostenida. La meta de producto es igualar la
calidad del modo híbrido sin red y sin convertir el MacBook Air en una placa caliente.

### Entrenamiento visual desde el notch

Los scripts `train_wake_word.sh` y `train_speaker_identity.sh` son precisos, pero todavía hablan el
idioma de un ingeniero. Una experiencia de entrenamiento dentro del notch/HUD podría guiar al usuario
con una frase por vez, mostrar ruido ambiental, diversidad y progreso, y activar el modelo solo cuando
la calidad sea suficiente. Esto convertiría un proceso técnico en una experiencia de un clic sin
debilitar sus controles.

### Ecosistema profesional de skills y MCP

Jarvis puede crecer con perfiles mínimos para Xcode, Slack, VS Code, Finder y navegadores. Cada perfil
debería cargar únicamente las herramientas necesarias para la aplicación frontal, usar Accesibilidad
o APIs oficiales primero y declarar qué acción requiere confirmación. El valor comercial no está en
tener miles de tools, sino en ofrecer pocas herramientas verificables que no roben foco ni soliciten
permisos destructivos.

### Evidencia para inversionistas

Antes de afirmar “producción” hacen falta métricas repetibles sobre el Mac objetivo: percentiles de
latencia, éxito de acciones, precisión del propietario, consumo de memoria, temperatura y recuperación
ante errores. El benchmark sintético 25/25 protege contratos; la calificación macOS viva demuestra el
comportamiento real. Ambos son necesarios y miden cosas diferentes.
