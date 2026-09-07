# ADR-0205: Resistencia prolongada separada de la voz física

- Estado: aceptado
- Responsable del requisito: Guillermo / gzambrano27
- Bloque: P7

## Contexto

P6 califica veinte flujos multiestado una vez. Una sola ejecución no detecta deriva acumulativa,
inicialización perezosa confundida con fugas, tareas asíncronas abandonadas ni degradación del IPC.
La prueba biométrica física, por otra parte, necesita la presencia y la voz del propietario; hacerla
parte de cada ciclo automatizado impediría una validación repetible y silenciosa.

## Decisión

Se crea `JarvisLongHorizonReliabilityBenchmark`. Ejecuta un calentamiento que debe aprobar, fuerza
recolección antes de fijar la línea base y después repite P6 veinte veces. Mide RSS actual —no solo el
pico histórico irreversible del proceso—, tareas pendientes, intentos de red y latencia por ciclo.

`p7_reliability_gate.sh` vincula esa evidencia al bundle instalado mediante su revisión exacta,
ejecuta el preflight completo de la beta y somete el daemon a un soak firmado compatible con los
estados activo, Low Power Mode y suspensión térmica. El gate no envía trabajos ni opera aplicaciones.

La calificación de voz del propietario queda excluida por defecto del harness y requiere la variable
explícita `AEGIS_RUN_OWNER_VOICE_QUALIFICATION=1`.

## Consecuencias

- Una carga perezosa inicial no se etiqueta falsamente como fuga persistente.
- Suspensión térmica o energética ya no puede aprobar con `cycles=0`; se prueban endpoints seguros.
- Un task abandonado, intento de red, crecimiento RSS superior a 8 MiB o p95 superior a dos segundos
  bloquea P7.
- El pre-push detecta regresiones acumulativas sin capturar la voz del usuario.
- Compatibilidad real por aplicación y precisión biométrica permanecen como evidencias separadas.
