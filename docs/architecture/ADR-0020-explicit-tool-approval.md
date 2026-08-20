# ADR-0020: aprobación explícita de herramientas

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** aprobar no debe repetir una inferencia que pueda cambiar los argumentos.
2. **Eliminar:** no se añade checkpoint externo, cola persistente, notificación ni framework UI.
3. **Simplificar:** el job conserva un único `ToolCall` y su autorización exacta durante dos minutos.
4. **Acelerar:** el grafo se detiene antes del synthesizer y el resultado TCP se resume localmente.
5. **Automatizar:** `jobs.approve` consume el grant, ejecuta, audita y reanuda el mismo job.

## Decisión

Una autorización `require_confirmation` detiene el grafo y cambia el job a
`awaiting_confirmation`. El snapshot expone solo herramienta, resumen acotado, digest y caducidad;
el `ToolCall` completo permanece en memoria del daemon. Múltiples aprobaciones, herramientas aún no
soportadas y conversaciones persistentes fallan cerradas en este MVP.

`jobs.approve` exige `job_id` y el digest exacto sobre el IPC autenticado. El daemon emite y consume
inmediatamente un grant de un solo uso, vuelve a autorizar la llamada almacenada y ejecuta sin pedir
al modelo que la regenere. Replay, digest distinto, expiración y reinicio invalidan la operación.
`jobs.cancel` constituye la denegación explícita.

El prompt del especialista líder permite proponer únicamente la herramienta mínima mediante una
llamada de función cuando necesita evidencia local actual. Aclara que solo el broker autoriza y
ejecuta, y prohíbe afirmar ejecución o inventar resultados. Los asesores paralelos no reciben
esquemas de herramientas y se les indica expresamente que ninguna está disponible.

La app mantiene su comportamiento `LSUIElement`. El menú solo muestra un indicador breve y abre una
ventana `Window` singleton cuando el usuario pulsa “Revisar aprobación…”. Esa ventana enseña el
resumen exacto, advierte que el sondeo es activo y ofrece aprobar una vez o denegar. No aparece de
forma automática y no registra parámetros ni identificadores.

## Encaje en el roadmap

- **Fase 4:** completa el consentimiento humano para el primer módulo activo de ciberseguridad.
- **Fase 5:** añade una superficie visual puntual sin convertir la app voice-first en un chat ni
  adelantar el HUD 3D.
