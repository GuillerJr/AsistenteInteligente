# ADR-0046: Reconciliación acotada del permiso de Micrófono

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** guardar el estado TCC solo para presentación deja desalineado el detector cuando
   el permiso cambia sin reiniciar Jarvis.
2. **Eliminar:** no se añaden observadores privados de TCC, polling nuevo ni reintentos periódicos.
3. **Simplificar:** la consulta existente de permisos compara el valor anterior con el actual.
4. **Acelerar:** conceder permiso activa el detector inmediatamente desde el flujo explícito y, como
   respaldo, en el siguiente sondeo ya existente.
5. **Automatizar:** revocar Micrófono cancela reanudaciones, detiene audio y deja el estado fail-closed.

## Decisión

`WakeWordAvailabilityPolicy` es una función pura reutilizable con tres resultados: `none`, `start` o
`stop`. Para TCC, solo devuelve `start` ante una transición desde cualquier estado no autorizado a
`authorized`, con opt-in, modelo válido, runtime íntegro y detector detenido. Mantener `authorized`
después de un fallo devuelve `none`, por lo que el monitor no crea un bucle de recuperación.

Una transición desde `authorized` a un estado no autorizado devuelve `stop` cuando la activación
sigue habilitada. También detiene cualquier detector residual aunque exista opt-out. La aplicación
conserva la preferencia, pero cancela las tareas de reanudación y recuperación hasta que TCC vuelva a
autorizar el micrófono.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** mantiene el stream alineado con TCC durante toda la sesión.
- **Fase 5 — Voice-first:** elimina el reinicio manual después de conceder el permiso.

## Consecuencia

Los cambios reales de autorización se aplican sin reiniciar Jarvis. Los fallos del analizador siguen
limitados por su compuerta de recuperación independiente y no se ocultan como cambios de permiso.
