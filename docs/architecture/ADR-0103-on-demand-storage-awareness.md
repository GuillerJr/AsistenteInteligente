# ADR-0103: almacenamiento local bajo demanda

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. Frases exactas sobre almacenamiento crean una llamada local `system_storage_status`, sin
   recuperar memoria ni invocar un modelo.
2. El broker la limita al planner, capacidad `system_read`, riesgo bajo y cero argumentos. El
   ejecutor consulta exclusivamente `/` mediante `os.statvfs`.
3. La salida contiene solo tres enteros consistentes: bytes totales, disponibles para el usuario y
   usados calculados. No expone nombres de volúmenes, rutas, archivos, snapshots o identificadores.
4. El grafo valida nuevamente el contrato y publica una frase española bajo
   `local/deterministic-storage`. Un fallo conocido también termina localmente y nunca cae en un
   modelo.
5. No se añade proceso, shell, red, permiso, caché, dependencia, monitor o *polling*.

## Motivo

El kernel ya conoce el espacio del volumen de inicio. Una inferencia no puede mejorar esa lectura y
añadiría latencia y exposición. En 100 recorridos completos del grafo sobre este Mac se observaron
cero llamadas de modelo y 2,19 ms de latencia promedio.

## Límites

- Solo se consulta el volumen accesible como `/`; no se enumeran discos externos ni otros mounts.
- La capacidad disponible puede diferir de Finder por espacio purgable, cuotas y snapshots APFS.
- No diagnostica crecimiento, limpia archivos ni recomienda eliminaciones.
- Preguntas abiertas sobre optimización o archivos grandes conservan el cerebro híbrido.
