# ADR-0012: Turno de voz explícito y efímero

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** una escucha continua no es necesaria para validar el primer flujo voice-first.
2. **Eliminar:** no se añaden wake word, grabador, historial, configuración, polling del job ni
   dependencia.
3. **Simplificar:** una acción `Hablar 8 s` reutiliza Apple Speech y el cliente IPC existentes.
4. **Acelerar:** el estado visible se limita a listo, escuchando, enviando, enviado o fallo.
5. **Automatizar:** la app comprueba daemon y permisos antes de cada turno y ejecuta el trabajo fuera
   del actor principal.

## Decisión

La acción solo está habilitada cuando daemon, micrófono y Speech están disponibles. La captura dura
ocho segundos, usa `es-US` y exige `requiresOnDeviceRecognition=true` desde `AegisAudioCore`.

Los eventos parciales se escriben en `/dev/null`. Solo el transcript final cruza temporalmente hacia
el cliente UDS autenticado; la UI no muestra texto, respuesta, secreto ni detalle interno de error.
No se inicia captura al arrancar ni por el monitor periódico.

## Encaje en el roadmap

- **Fase 3:** completa el primer turno sensorial de voz de extremo a extremo.
- **Fase 5:** añade la acción voice-first mínima sin adelantar HUD, wake word ni respuesta hablada.

## Consecuencia

El MVP puede enviar una orden hablada al enjambre sin persistir audio o transcripciones. El host aún
requiere que el usuario conceda TCC y disponga del asset on-device de Apple Speech.
