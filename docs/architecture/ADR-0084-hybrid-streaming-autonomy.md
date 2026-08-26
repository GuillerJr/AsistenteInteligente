# ADR-0084: cerebro híbrido, streaming e independencia controlada

- Estado: aceptado
- Fases: 1, 2, 3, 4 y 5

## Decisión

1. Las solicitudes breves, textuales y sin herramientas intentan primero Apple Foundation Models
   mediante un helper Swift privado, acotado y alojado en el bundle. El resto escala a los roles
   NVIDIA existentes. Un fallo local conmuta al remoto; nunca amplía autoridad.
2. Apple y NVIDIA emiten deltas monotónicos. El job conserva una instantánea parcial versionada y
   la app pronuncia solo frases completas. Una nueva detección local de «Jarvis» cancela el job y la
   salida de voz antes de abrir otro turno.
3. La memoria persistente añade evidencia, confianza, última confirmación y caducidad. Los registros
   vencidos no participan en FTS ni RAG. Solo texto explícito o una voz local verificada actualizan
   el perfil del propietario.
4. La autonomía conserva una acción como máximo por resultado, política deny-by-default,
   confirmación de un solo uso y auditoría. `shortcut_run` solo acepta el nombre exacto de un atajo
   existente y ejecuta el binario nativo de macOS, sin shell, archivos ni argumentos libres.
5. Todo job terminal genera una evaluación operativa en memoria: cerebro, modelo, latencia total,
   primer fragmento, fragmentos, herramienta y éxito. Los agregados se consultan por IPC; no se
   persisten contenidos.

## Motivo

El modelo local reduce latencia y exposición para la conversación común, mientras NVIDIA conserva
el trabajo especializado que justifica red y costo. El contrato único de stream evita añadir un
framework realtime, la memoria evoluciona sin una segunda inferencia y Shortcuts ofrece integración
nativa extensible sin incorporar SDKs por aplicación.

## Límites de seguridad

- El helper debe pertenecer al UID actual, ser ejecutable y negar escritura a grupo/otros.
- La ruta local no recibe imágenes, herramientas ni solicitudes largas.
- Los deltas, métricas y estados tienen límites de bytes y versiones monotónicas.
- Interrumpir cancela la ejecución; no concede una aprobación pendiente.
- Ninguna autoevaluación ejecuta una acción o modifica una política por sí sola.
