# ADR-0155: compuerta sensible posterior a una acción local

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

El fast-path comprobaba `secure_content` antes de actuar. Sin embargo, un clic, scroll o tecla podía
revelar un login, campo seguro o texto sensible. La recaptura lo interpretaba como progreso visual y
podía informar objetivo completado.

## Decisión

1. Tras toda acción local, `secure_content` se comprueba antes de dHash o semántica Accessibility.
2. Una transición segura→sensible termina como `blocked/sensitive_action` después de contar la única
   acción ya ejecutada. No reintenta, no llama a NVIDIA y no informa éxito.
3. `ComputerPerception` rechaza cualquier elemento con `sensitive=true` si la bandera global no está
   activa. Una bandera global activa sin elemento visible sigue permitida por truncamiento.
4. No se añade captura: se reutiliza la observación posterior exigida por la prueba de progreso.

## Consecuencia

Mostrar contenido sensible deja de ser una señal positiva de progreso. La protección funciona tanto
para campos seguros Accessibility como para términos sensibles detectados localmente por Vision y
no añade dependencia, token, persistencia, permiso o latencia de red.
