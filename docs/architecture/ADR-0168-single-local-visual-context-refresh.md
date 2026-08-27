# ADR-0168: una recuperación local de contexto visual

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

ADR-0167 impide ejecutar una acción cuando la ventana observada cambia de posición, tamaño, proceso
o display. El helper devolvía ese rechazo como objetivo inseguro genérico, por lo que un movimiento
inocuo terminaba toda la sesión y desperdiciaba la decisión ya validada. Reconsultar el modelo añade
latencia, coste y exposición de otra captura, pero repetir sin observar relajaría el límite de
seguridad.

## Decisión

1. Una diferencia del contexto se informa únicamente como `computer_observation_changed`; los
   bridges nativo y relay conservan ese motivo exacto.
2. El controlador no repite de inmediato. Captura otra observación de la misma aplicación y obtiene
   un contexto nuevo del helper firmado.
3. Solo una acción determinista producida por el fast-path de una orden local exacta puede ejecutarse
   una vez con el contexto nuevo. La percepción no puede contener contenido seguro, el texto debe
   continuar ligado al objetivo aprobado y cualquier clic conserva la misma etiqueta, centro y
   propiedad `pressable` de Accessibility.
4. Una decisión producida por visión remota nunca se reutiliza después de cambiar el contexto. La
   recuperación local no llama a NVIDIA, no modifica el prompt y no persiste ni registra ninguno de
   los dos contextos.
5. Un segundo cambio de contexto, un control distinto o una percepción sensible detienen la sesión.
   No existe un tercer intento ni un bucle de recuperación.
6. La observación realmente usada para actuar sustituye a la original al verificar progreso y
   evidencia posterior.

## Consecuencia

Mover o redimensionar una ventana ya no aborta necesariamente una navegación válida, pero tampoco
autoriza una acción sobre la escena anterior. La ruta normal mantiene una captura y un intento; solo
la carrera detectada paga una recaptura local, con un límite absoluto de un reintento.
