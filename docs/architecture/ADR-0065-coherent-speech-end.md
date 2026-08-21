# ADR-0065: Fin de voz coherente

- Estado: aceptado
- Fase: 3 — Capacidades sensoriales
- Fecha: 2026-08-21

## Contexto

El inicio de voz ya exigía una muestra con `voice_active=true`, pero el final podía viajar con el
flag todavía activo. Esa combinación no la produce el detector Swift y permitía que el daemon
cerrara un turno mientras la telemetría afirmaba que había voz.

## Decisión

Un evento `ended` debe estar unido a una muestra con `voice_active=false`. La comprobación ocurre
antes de almacenar la muestra, renovar la lease, incrementar el contador o cambiar el turno a
`idle`.

## Filtro del algoritmo de ingeniería

1. Se cuestionó aceptar dos campos contradictorios del mismo productor.
2. Se descartaron tolerancias, configuración y estado duplicado.
3. Se reutiliza el booleano que ya cruza el IPC.
4. La ruta añade una sola comparación.
5. Todos los productores heredan el rechazo atómico sin coordinación adicional.

## Consecuencias

Inicio y fin quedan simétricos respecto a `voice_active`. Un productor defectuoso conserva el
último estado válido y debe corregir su siguiente publicación o dejar expirar la sesión.
