# ADR-0080: Relay de control atribuido a Jarvis

- Estado: aceptado
- Fases: 1, 4 y 5

## Contexto

Una prueba real mostró que un helper iniciado por el LaunchAgent Python hereda a Python como proceso
responsable ante TCC. Por ello `CGPreflightScreenCaptureAccess()` fallaba dentro del job aunque el
usuario hubiera autorizado Jarvis y `JarvisComputerHelper`.

## Decisión

El daemon conserva planificación, política, aprobación, límites y ciclo visual. Para cada operación
nativa publica una sola orden efímera mediante `computer.wait`; la app Jarvis ya residente la obtiene
por el socket HMAC, verifica la firma del helper, lo ejecuta por JSON stdin y devuelve el diccionario
acotado con `computer.complete`.

La cola admite una orden, el comando no supera 8 KiB, la respuesta no supera 60 KiB y los timeouts
siguen siendo 20 segundos para activación y ocho para captura/acción. Una orden vencida o una
respuesta repetida no puede reactivarse. Capturas, argumentos y resultados no se registran ni se
escriben en disco.

El panel vuelve a consultar al helper cuando se presenta o recupera foco. No infiere autorización a
partir del interruptor visible de Ajustes del Sistema: `CGPreflightScreenCaptureAccess()` y
`AXIsProcessTrusted()` son la fuente de verdad. En builds ad hoc, cada binario tiene un requisito
designado ligado a su hash; tras reinstalar, macOS puede mostrar la entrada anterior activa aunque el
binario nuevo todavía no esté autorizado. Para conservar TCC entre builds se configura una identidad
estable mediante `AEGIS_CODESIGN_IDENTITY`.

El entorno local puede crear `Jarvis Local Development` mediante
`script/local_codesign_identity.sh`. La identidad se guarda en el llavero de inicio de sesión, su
clave se importa como no exportable y el ACL autoriza solo a `/usr/bin/codesign`. El build la detecta
automáticamente; una identidad configurada explícitamente conserva prioridad. La confianza se limita
a la política `codeSign` del usuario y no convierte el bundle en una distribución notarizada.

Accesibilidad se verifica y solicita en los dos participantes: la app anfitriona Jarvis y el helper.
Esto es necesario porque TCC puede atribuir una operación del proceso hijo al responsable que lo
creó. La capacidad solo queda `ready` cuando ambos responden como confiables; autorizar únicamente
la entrada visual del helper falla cerrado.

## Aplicación del algoritmo de ingeniería

1. **Cuestionar:** autorizar Python no representa la identidad del producto y se rompe al actualizar
   Homebrew.
2. **Eliminar:** no se añade XPC, otro daemon, RPA ni una segunda credencial.
3. **Simplificar:** se reutilizan Jarvis, el socket, HMAC, Keychain y el helper existentes.
4. **Acelerar:** una espera larga permanece inactiva hasta que aparece una orden.
5. **Automatizar:** tras la aprobación, el relay opera sin clics adicionales y falla cerrado si
   Jarvis no está disponible.

## Consecuencias

- TCC atribuye Screen Recording a la app Jarvis que el usuario reconoce.
- Cerrar Jarvis impide el control visual; el daemon agota el timeout sin ejecutar una alternativa.
- El modelo nunca accede al relay ni puede omitir Tool Broker, digest o confirmación de un solo uso.
- La tarjeta `CONTROL` identifica por separado si falta pantalla, Accesibilidad o el helper, y se
  refresca automáticamente al volver desde Ajustes del Sistema.
