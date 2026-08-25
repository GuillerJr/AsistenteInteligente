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
