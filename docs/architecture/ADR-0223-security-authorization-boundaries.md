# ADR-0223: consentimiento exacto, caducidad y bloqueo por integridad

Fecha: 2026-09-14. Estado: aceptado.

## Decisión

La autoridad permanece en las políticas locales, no en el modelo, la memoria ni la transcripción.
Una aprobación liga el digest recibido, el esperado, la llamada retenida y la autorización
pendiente. Tras consumir el grant, la nueva autorización debe conservar identidad, herramienta y
argumentos normalizados. La comparación usa JSON canónico, distingue booleanos de números y no
depende del orden de las claves. Un bloque `finally` revoca cualquier grant no consumido, incluso
si la política deniega, cambia los argumentos o lanza una excepción.

El almacén de grants conserva consumo, revocación y caducidad durante toda su vigencia. Al alcanzar
4096 entradas retira solo grants caducados y sus marcas asociadas. Grants y nonces IPC mantienen
una marca de tiempo máxima observada: atrasar el reloj no permite revivir registros retirados.
La solicitud pendiente usa además el inicio monotónico ya disponible para imponer dos minutos
independientemente del reloj de pared. No se introduce polling ni un temporizador adicional.

La aprobación por voz comprueba primero que el job/digest siga pendiente; tras la verificación
asíncrona vuelve a comprobarlo bajo el lock del gestor. La evidencia exige booleanos reales y una
probabilidad finita en [0, 1]; la identidad conserva el umbral existente. El operador permite una
palabra afirmativa del catálogo existente, tilde y puntuación afirmativa acotada, no preguntas,
citas, negaciones o frases compuestas. No se usa un LLM para decidir consentimiento.

Solo el propietario verificado reserva un hash de PCM antes de transcribir. La caché admite 512
reservas y quince minutos de vigencia; la saturación rechaza antes de transcribir y no expulsa
hashes activos. Cancelación, error o rechazo semántico conservan la reserva. El resultado de una
transcripción caducada o con reloj monotónico inválido/regresivo no concede autoridad.

`PolicyContext` incorpora una consulta del estado de integridad. El daemon la conecta a su mismo
`SecurityStateLatch`; la ausencia del callback mantiene compatibles los contextos aislados de
biblioteca. Cuando existe, solo un resultado literal `False` permite continuar: un error de
consulta falla cerrado. Broker, ejecución aprobada, despacho asíncrono/worker, recuperación visual
y pasos de dispositivo del planificador consultan esa compuerta antes de entrar en la operación.

## Límites

Las reservas y marcas temporales viven en RAM. No se promete protección persistente entre
reinicios, detección de audio transformado, liveness biométrico ni deshacer operaciones ya
iniciadas. Se conservan HMAC, TCC, Keychain, firma, permisos, catálogo de herramientas y elección
de proveedores. El CLI sigue siendo exclusivamente NVIDIA. No se guardan datos biométricos
adicionales ni textos de aprobación en logs.

Evidencia y reproducción en
[SECURITY_AUTHORIZATION_POLISH.md](../quality/SECURITY_AUTHORIZATION_POLISH.md).
