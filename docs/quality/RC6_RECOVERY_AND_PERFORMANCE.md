# RC-6 — Recuperación y rendimiento operativo

RC-6 mata de forma controlada el daemon empaquetado solamente cuando no existen agentes activos.
El supervisor nativo debe levantar un PID distinto, restaurar el UDS y conservar
`security.status=intact`. Esto demuestra recuperación real del proceso, no una simulación.

Después del reinicio se consulta la telemetría privada: latencia activa, tiempo de pared, RSS,
estado térmico y métricas de trabajos. El reporte excluye solicitudes, URLs, conversaciones y
transcripciones.

## Criterio de cierre

- El daemon rechaza el reinicio si está ocupado.
- El nuevo PID responde por el canal HMAC y mantiene la cadena de seguridad intacta.
- La telemetría puede leerse después de la recuperación.
- El soak posterior mantiene p95 menor o igual a 250 ms y crecimiento RSS menor o igual a 8 MiB.

La orden canónica es `./script/rc3_rc8_gate.sh`.
