# ADR-0169: una nueva decisión remota tras reobservar

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

El contexto de ADR-0167 detecta que una decisión visual perdió su vínculo geométrico. ADR-0168
recupera localmente órdenes deterministas, pero reutilizar una acción elegida desde una captura
anterior permitiría trasladar su significado a otro estado o proceso. Detener siempre la sesión es
seguro, aunque convierte un movimiento inocuo de ventana en un fallo evitable.

## Decisión

1. El primer `computer_observation_changed` de una acción remota descarta esa acción por completo.
2. Jarvis recaptura la aplicación autorizada mediante el helper firmado. Si la percepción contiene
   contenido seguro, termina antes de contactar al proveedor.
3. Visión recibe la captura y percepción nuevas, el mismo objetivo aprobado y el mismo número de
   paso. El contexto SHA-256 continúa fuera del prompt.
4. La acción nueva atraviesa otra vez el esquema estricto, el vínculo literal de escritura, el
   control Accessibility exacto y todas las barreras sensibles antes de llegar al helper.
5. La reobservación no incrementa el número de acciones ejecutadas ni amplía `max_steps`. Una espera
   explícita y cada acción correcta sí consumen un paso.
6. Solo existe una redecisión remota por sesión. Otro cambio de contexto termina como estado incierto
   sin una tercera captura ni otro intento.
7. Tras ejecutar la acción fresca, la recaptura y verificación de progreso existentes siguen siendo
   obligatorias, incluso si era el último paso permitido.

## Consecuencia

Jarvis tolera una carrera geométrica sin ejecutar una decisión vieja ni conceder efectos adicionales.
La ruta normal no añade latencia; únicamente la carrera detectada paga una captura y una inferencia
de visión adicionales, ambas acotadas por el timeout global de la sesión.
