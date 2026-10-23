# Primera conexión: Codex con ChatGPT (23-10-2026)

El dueño retiró la exclusividad NVIDIA y eligió esta conexión primero. La decisión
está en [ADR-0235](../architecture/ADR-0235-configured-inference-and-codex-chatgpt.md).

## Implementación

- El daemon y el CLI usan un único transporte de inferencia configurado: el cliente
  oficial Codex App Server. La sesión se inicia desde Ajustes y la completa el dueño.
- Estado y HOME propios, directorio vacío, perfil de solo lectura sin red para
  comandos y verificación de permisos efectivos. Ninguna herramienta nativa de
  Codex sustituye al broker de Jarvis; las propuestas pasan sus validaciones.
- Catálogo real de modelos, selección persistente y guardado explícito. Un modelo
  retirado deja visible el catálogo para poder repararlo, sin reemplazo silencioso.
- IPC de configuración saneada: solo estado y metadatos de modelos. El enlace de
  acceso se entrega con el recibo inicial; no se publican credenciales ni la cuenta.
- Cancelación coordinada, plazos inferiores a los del cliente nativo y bloqueo de
  cambios de modelo/sesión mientras hay trabajos activos.
- Los embeddings usan Apple con su identidad de índice. No se mezclan vectores NVIDIA.
  El daemon y el CLI productivos no inicializan clientes NIM ni consultan su clave.
- Los permisos persistentes de control de apps usan un almacén nuevo: el permiso
  anterior para enviar observaciones a NVIDIA no se reutiliza para Codex.
- Voz y notas de audio de Telegram quedan sin configurar. El chat funciona de forma
  independiente. Readiness 3.0 declara `voice_output`; las calificaciones de voz
  rechazan evidencia antigua si falta un proveedor de audio operativo.
- La evaluación y las métricas distinguen Codex de los registros NVIDIA heredados.

## Cobertura y comprobaciones

Pruebas del protocolo JSON-RPC, perfil aislado, credenciales ausentes, inicio y
cancelación de sesión, eventos de acceso fallido, catálogo, selección retirada,
visión, salida estructurada, herramientas no ofrecidas, respuestas incompletas,
interrupción y cierre. Pruebas del contrato IPC y SwiftUI, cancelación durante el
inicio de sesión y conservación del texto cuando falta salida de voz.

El arranque aislado con Codex CLI 0.163.0 confirmó el perfil efectivo y una sesión
efímera sin iniciar sesión ni enviar una consulta. Las pruebas del daemon usan
secretos sintéticos y prohíben inicializar NVIDIA; verifican chat/ingeniería,
procedencia Codex, audio sin configurar y una respuesta de configuración que supera
el plazo genérico de cuatro segundos.

Validación reproducible del candidato:

```bash
.venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/python -m pytest -p no:warnings
./script/test_native.sh
.venv/bin/python script/sync_localizations.py --check
./script/aegis.sh release-scope
```

Los hooks conservan la suite completa, empaquetado Release y los contratos de
aceptación, flujos productivos y fiabilidad. Esos contratos deterministas no
representan consultas reales a la cuenta del dueño.

## Comprobación que necesita al dueño

Tras instalar la app firmada: Ajustes › Modelos de IA › Iniciar sesión con ChatGPT ›
Abrir inicio de sesión. El dueño completa el acceso oficial. Después se ejecuta
«Probar conexión» y se verifica un chat temporal sintético desde la app.

El dueño elige los permisos de TextEdit y los retira desde Ajustes. El recorrido
real de «Siempre», otro chat y revocación se completó con `36bcc60`, como se detalla
abajo. Las pruebas simuladas y el cambio de proveedor no sustituyen esa comprobación.
El audio, Ollama y otras APIs no forman parte de esta primera conexión.

## Verificación real de la primera conexión

La revisión `291d0c6` se publicó en main y pasó CI, controles locales, empaquetado
y `jarvis_beta.sh check`. La instalación conserva firma Jarvis Local Development
y el ejecutable mide 6.118.496 bytes. El dueño completó el acceso oficial; no fue
necesario pedir ni leer su contraseña en el chat.

Pasaron la prueba de conexión, una respuesta en chat temporal y una imagen sintética
con el número 4827, un cuadrado azul y dos círculos naranjas. Con GPT-5.6-Luna, elegido
por el dueño, un chat temporal nuevo ejecutó `system_power_status`; los únicos campos
consultados de la última evaluación técnica confirmaron Codex, esa herramienta y
`outcome_verified=true`, sin leer conversaciones ni argumentos.

Dos consultas de batería en otro contexto terminaron con una negativa del modelo.
Los cinco contrastes sintéticos del adaptador sí propusieron la herramienta y el
grafo/servicio con datos sintéticos conservó el catálogo. La causa de esas negativas
no está confirmada; no se declara corregida ni se fuerza `auto` para ocultarla.

## Correcciones comprobables posteriores a la instalación

La petición «En TextEdit, escribe…» dejaba vacío el catálogo porque el clasificador
solo admitía el verbo antes del destino. Se amplía esa gramática conservando la
identidad de la app, el rechazo de destinos ajenos y la aprobación del dueño.
La regresión debe llegar a confirmación pendiente sin ejecutar nada.

El esquema de salida de Codex excluye propuestas vacías cuando el llamador exige
una herramienta; las elecciones automáticas permanecen opcionales. La auditoría
local registra contadores de herramientas ofrecidas y propuestas y un tipo de
selección cerrado, sin nombres, argumentos ni contenido, para poder distinguir
catálogo vacío de una respuesta que no propone una llamada.

## TextEdit: permiso permanente y revocación verificados

En la app firmada `36bcc60`, con GPT-5.6-Luna y el historial oculto, el dueño eligió
«Siempre» en la tarjeta nativa. Apareció el recibo de permiso guardado. El primer
intento se detuvo por `user_takeover`; se observó el documento todavía vacío, sin
atribuir esa actividad a una persona o proceso concreto.

Un chat temporal nuevo reutilizó el permiso sin volver a pedirlo y, tras un reintento
de conexión, escribió exactamente «PRUEBA JARVIS CODEX A». El árbol de accesibilidad
del único documento sintético mostró ese literal. Los campos técnicos de la evaluación
del 23-10 a las 20:19:54 UTC confirmaron `brain=codex`, `tool_name=computer_use`,
`succeeded=true` y `outcome_verified=true`.

El dueño retiró TextEdit en Ajustes y cerró esa pantalla sin que el agente la leyera.
Otra petición explícita de escritura mostró de nuevo la aprobación pendiente; el
documento conservó solo A. Se detuvo la solicitud sin conceder autorización. Esto
cierra P1-5/R2-3 para el recorrido de TextEdit y conserva la revocación del dueño.

La variante «En TextEdit, añade al final una nueva línea…» quedó fuera de la gramática
y no ofreció herramientas; esa negativa no sirve como prueba de revocación. El control
actual tampoco admite el salto de línea en `type`, y `field_contains` no certifica la
posición final ni la conservación íntegra del texto. Se deja esa función como pendiente
específico, sin ampliar solo el enrutador para prometer una operación incompleta.

También se identificó una carrera: al cancelar una síntesis, el cierre ocurría después
de liberar el bloqueo de inferencia y podía interrumpir el planificador del mensaje nuevo.
Ahora la limpieza conserva ese bloqueo y se coordina con arranque, consultas y cierre
explícito. Las regresiones con tareas y eventos reproducen el fallo previo, prueban la
cancelación repetida y comprueban que cancelar una petición en espera no cierre la activa.
No se debilitan las aprobaciones ni la detección de actividad.
