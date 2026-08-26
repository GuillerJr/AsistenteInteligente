# ADR-0118: Contactos y Recordatorios locales con escritura confirmada

## Estado

Aceptado.

## Decisión

1. Jarvis reutiliza el ejecutor JXA endurecido: scripts fijos por entrada estándar, sin shell ni
   código generado por modelos.
2. `reminders_list` devuelve como máximo 50 títulos, listas, vencimientos y estados. Nunca devuelve
   notas y deja fuera los completados por defecto.
3. `contacts_search` examina como máximo 2.048 candidatos y devuelve como máximo 20 nombres, tres
   correos y tres teléfonos por resultado. Nunca devuelve notas, direcciones, cumpleaños o IDs.
4. `reminder_create`, `reminder_complete` y `contact_create` requieren confirmación de un solo uso.
   El ejecutor vuelve a comprobar que la confirmación fue consumida.
5. Completar un recordatorio requiere una coincidencia exacta y única entre pendientes; cero o más
   de una coincidencia fallan sin modificar nada.
6. Las órdenes exactas de lectura se enrutan y formatean localmente, sin NVIDIA ni sintetizador.

## Consecuencias

- macOS conserva el control TCC y puede pedir Automatización en el primer uso.
- La superficie es menor que una integración CRUD genérica: no se admiten notas, consultas libres,
  borrado ni modificación masiva.
- Una agenda de contactos mayor a 2.048 personas falla cerrada; ampliar el límite requiere otra
  revisión de privacidad y rendimiento.
