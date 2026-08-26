# ADR-0100: reloj local determinista sin herramienta

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. Preguntas exactas y acotadas sobre hora, fecha o ambas producen directamente un `AgentResult`
   `local/deterministic-clock` en el enrutador.
2. La fecha se obtiene con `datetime.now().astimezone()` y se expresa en español, con día de semana,
   mes y reloj de 24 horas. Las pruebas inyectan exclusivamente timestamps con zona horaria.
3. El grafo publica el texto como único fragmento y resultado final. No recupera memoria, ofrece
   herramientas, abre procesos ni invoca Apple Foundation Models o NVIDIA.
4. Adjuntos, transcripción no local, `force_remote` y preguntas sobre otra zona horaria quedan fuera
   del atajo y conservan el flujo normal.

## Motivo

El proceso ya posee una fuente autoritativa para la hora local. Crear una herramienta, consultar el
sistema mediante shell o pedir a un modelo que la repita agregaría componentes sin aportar
información. En 1.000 recorridos completos del grafo se observaron cero llamadas de modelo y
2,89 ms de latencia promedio.

## Límites

- La exactitud depende del reloj y la zona horaria configurados en macOS.
- No convierte zonas horarias, calcula intervalos, interpreta fechas ambiguas ni programa alarmas.
- No se crea una entrada de auditoría de herramienta porque no existe ejecución externa ni cambio de
  estado.
