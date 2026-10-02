# ADR-0231: Intenciones espaciales de la mascota mediante NVIDIA

## Decisión

La mascota recibe destinos generados por NVIDIA y los recorre con un motor gráfico
local de llegada, aceleración limitada y límites de colisión. El motor no contiene
un modelo de IA ni rutas aleatorias o animaciones de desplazamiento pregrabadas.
Las reacciones inmediatas al cursor y al contacto son reflejos de interacción.

La aplicación describe únicamente geometría numérica, posición, cursor dentro del
notch, fase real de Jarvis y memoria física propia (energía, curiosidad, afinidad y
hasta seis destinos visitados). No envía capturas, mensajes, archivos o credenciales.
El proveedor y los modelos provienen de la configuración NVIDIA existente. El JSON
solo admite cinco intenciones, de uno a tres destinos normalizados y velocidad y
duración acotadas. No concede herramientas, permisos o acciones del asistente.

El espacio visible vive debajo de la cámara y de la barra de menús. El área
expandida mide como máximo 280 × 208 puntos; el cálculo del centro descuenta el
cuerpo, los bordes y las etiquetas. Las coordenadas se adaptan a los tamaños
intermedios de la expansión sin reiniciar posición o velocidad. Se mantienen los
gestos de contacto, fijación y doble clic para voz, sin añadir otro chat o menú.

## Límites y ciclo de vida

La simulación gráfica funciona hasta a 60 Hz; solo su subvista observa la posición.
Las decisiones remotas usan tickets IPC independientes, una inferencia concurrente,
12 segundos de presupuesto, al menos 30 segundos entre admisiones y un máximo de
60 solicitudes por hora. La app normalmente pide una decisión cada 60 segundos;
un fallo añade 90 segundos de espera. Los resultados expiran a los 30 segundos.
No hay llamadas de red por fotograma y una desconexión no congela el renderizador.

Las tareas del usuario, incluida una aprobación pendiente, impiden nuevas consultas
y cancelan la inferencia de la mascota en curso sin esperar a que la UI consulte su
resultado. El estado real de trabajo prevalece sobre la intención estética. Cerrar
el panel cancela sus tareas y reloj; bloquear la sesión descarta el plan. Seguridad
comprometida y suspensión térmica invalidan respuestas. El ahorro de energía no
suspende el asistente ni este servicio. Reducir movimiento desactiva desplazamientos
y consultas y baja el reloj a 1 Hz, manteniendo los estados accesibles.

Se conservan posición y memoria física validada en UserDefaults (máximo 2 KiB).
Los planes remotos nunca se restauran. El registro de diagnóstico solo informa
proveedor, intención y número de destinos, sin contenido privado.

## Verificación

Pruebas de límites físicos, destinos múltiples, expansión, continuidad al cambiar
el objetivo, cursor/contacto, cuadros retrasados, reducción de movimiento y
restauración sin planes. Pruebas del servicio para JSON inválido, campos privados,
prioridad de trabajos reales, cancelación, cierre, expiración y presupuestos.
La prueba optativa `AEGIS_RUN_NVIDIA_MASCOT_PROBE=1` usa exclusivamente geometría
sintética para comprobar una decisión real de exploración del proveedor NVIDIA.
