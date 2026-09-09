# RC-1 y RC-2 — Estabilización previa al candidato

Este bloque corrige dos riesgos observados en ejecución real sin depender de muestras de voz. No
reemplaza P10: la calificación física del propietario continúa aplazada hasta disponer de un entorno
acústico controlado.

## RC-1 — Latencia y continuidad del runtime

### Problema observado

El cerebro Apple ya había producido una respuesta local válida, pero las solicitudes clasificadas
como complejas esperaban al especialista NVIDIA dentro del camino crítico. Cuando el proveedor no
entregaba un primer fragmento, el usuario recibía la respuesta local entre 20 y 27 segundos después.
Además, la aplicación no reiniciaba el daemon incluido si este terminaba inesperadamente.

### Garantías implementadas

- Toda escalada que ya dispone de respuesta local concede al proveedor remoto un presupuesto de
  1,5 segundos para entregar su primer fragmento útil.
- Los fragmentos remotos se mantienen privados hasta decidir qué proveedor será visible. Así se
  evita mezclar media respuesta local con media respuesta remota.
- Si el plazo vence, la solicitud remota se cancela y la respuesta local se libera inmediatamente.
- Las rutas registran latencia local, latencia remota y solo la clase acotada del error; nunca
  prompts, tokens, credenciales ni cuerpos del proveedor.
- Los fallos sin ruta segura se convierten en códigos públicos diferenciados:
  `brain_unavailable` y `remote_provider_unavailable`.
- El daemon incluido tiene recuperación acotada: reintentos a 250 ms, 1 s y 4 s. Un proceso estable
  durante 60 s recupera el presupuesto; tres reinicios fallidos consecutivos exigen intervención.

## RC-2 — Operabilidad y diagnóstico honesto

### Problema observado

CLI, notch y HUD reducían fallos distintos a mensajes genéricos, y trataban una credencial NVIDIA
ausente como si Jarvis completo estuviera caído incluso cuando Apple Intelligence funcionaba.

### Garantías implementadas

- El CLI traduce fallos estables a una explicación y una acción de recuperación concreta.
- El notch, HUD y menú muestran el fallo real del último turno sin exponer excepciones internas.
- Una instalación con cerebro Apple disponible permanece saludable aunque NVIDIA no esté
  configurada. La ausencia del proveedor opcional ya no genera una falsa alarma naranja.
- Los errores de permisos TCC, integridad, timeout, respuesta incompleta y disponibilidad del
  cerebro tienen diagnósticos distintos.
- El aviso de compatibilidad conocido de LangGraph se silencia en el límite del paquete mediante
  coincidencia exacta; cualquier otra advertencia continúa visible.

## Verificación sin voz

La aceptación de este bloque exige:

1. pruebas Python del router híbrido, política de fallos y CLI;
2. pruebas Swift del supervisor, feedback seguro y contratos nativos;
3. compilación del bundle macOS;
4. calificación macOS con puntuación 100;
5. prueba textual contra el daemon instalado para medir que NVIDIA lento ya no retenga una
   respuesta local.

P10 y la parte física de P11 siguen pendientes por decisión explícita: requieren voz real del
propietario y se ejecutarán al final, en silencio ambiental suficiente.
