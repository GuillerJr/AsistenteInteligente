# ADR-0149: reparación única de una decisión visual inválida

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

El cliente NVIDIA ya usa el modelo visual alterno ante errores HTTP o una respuesta NIM vacía. Sin
embargo, una respuesta de chat válida cuyo contenido no satisfacía `ComputerAction` terminaba toda
la sesión, aunque ninguna acción se hubiera ejecutado todavía.

## Decisión

1. Cada paso admite un intento normal y una sola reparación cuando el JSON no cumple el contrato.
2. La reparación reutiliza la observación actual y añade únicamente una instrucción fija para
   devolver uno de los objetos permitidos.
3. La salida inválida no se incluye en la segunda solicitud, registros, auditoría ni estado.
4. No se ejecuta ninguna acción antes de obtener un `ComputerAction` completamente validado.
5. Un segundo fallo termina como `computer_invalid_decision`; no existe otro retry ni relajación del
   esquema, permisos, aplicación autorizada, límites o política.

## Consecuencia

Una desviación ocasional de formato deja de abortar una sesión segura. El caso normal no añade red
ni latencia, y el caso defectuoso queda limitado a una llamada adicional dentro de los 90 segundos
ya asignados a `computer_use`.
