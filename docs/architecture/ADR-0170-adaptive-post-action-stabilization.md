# ADR-0170: estabilización visual adaptativa postacción

- Estado: aceptado
- Fases: 1, 3 y 5

## Evidencia

El controlador esperaba 450 ms antes de observar cualquier efecto local o remoto. Muchas interfaces
ya terminan su transición mientras el helper valida y entrega la acción, por lo que la pausa se
pagaba incluso cuando una captura inmediata podía demostrar progreso. Eliminarla sin respaldo haría
que animaciones o respuestas lentas parezcan acciones fallidas.

## Decisión

1. Después de una acción correcta, Jarvis captura inmediatamente la misma aplicación autorizada.
2. Si la captura contiene `secure_content`, se devuelve de inmediato a la compuerta sensible. No
   espera ni realiza otra captura.
3. Si la semántica Accessibility cambió o el dHash difiere al menos 8 bits, la observación se acepta
   como estado postacción sin pausa adicional.
4. Solo cuando la primera captura no demuestra progreso, Jarvis espera el intervalo acotado de 450 ms
   y captura una segunda y última vez.
5. Acciones locales y remotas comparten el mismo método. Las esperas explícitas del modelo no lo usan
   porque no ejecutaron una mutación que verificar.
6. La captura inmediata o final sigue alimentando las compuertas sensibles, evidencia nueva,
   detección de repetición y límite de pasos existentes. No se persiste ninguna imagen o firma.

## Consecuencia

El camino común elimina 450 ms por acción cuando la interfaz ya respondió. Las aplicaciones lentas
mantienen el margen anterior y pagan una sola captura adicional. No cambian permisos, proveedor,
autoridad de acción ni criterios de progreso.
