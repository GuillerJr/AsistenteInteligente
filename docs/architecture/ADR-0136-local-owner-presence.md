# ADR-0136: presencia local reciente para contexto privado de voz

- Estado: aceptado
- Fases: 2, 3 y 5

## Decisión

1. La identidad de hablante solo selecciona el perfil. Un turno hablado obtiene memoria, historial,
   gustos y contexto relacional únicamente cuando además existe una prueba reciente de presencia
   del dueño del dispositivo.
2. La app mantiene en RAM una concesión de 30 minutos basada en el reloj monotónico. Se crea al
   desbloquear la sesión macOS o tras una autenticación nativa con `LocalAuthentication`. Un arranque
   o reinicio de la app no prueba por sí mismo que la pantalla esté desbloqueada. Nunca se persiste ni
   se renueva por actividad de voz.
3. Al vencer, el siguiente turno reconocido como propietario solicita Touch ID o la contraseña del
   dispositivo. Cancelar o fallar no bloquea la respuesta: elimina las marcas privadas y procesa el
   turno en una conversación aislada.
4. Bloquear o abandonar la sesión revoca inmediatamente la concesión, cancela captura, síntesis,
   job y control en curso, oculta el puntero de Jarvis y detiene el detector de activación. Al
   desbloquear, el detector puede reanudarse según sus compuertas existentes.
5. El daemon exige simultáneamente `owner_speaker_profile`, `owner_presence_verified` y confianza
   local suficiente. El antiguo indicador de perfil único no concede por sí solo acceso privado.
6. La presencia viaja solo en el IPC local autenticado. No autoriza herramientas, no sustituye las
   confirmaciones de acciones y no convierte el clasificador de voz en autenticación biométrica.

## Motivo

Una grabación puede superar un clasificador de hablante cuando el Mac queda desatendido. La sesión
de macOS ya dispone de una señal nativa de presencia del dueño; reutilizarla mediante una concesión
breve y monotónica cierra ese acceso sin agregar polling, otro modelo, persistencia o una dependencia.

## Consecuencias

- Jarvis continúa respondiendo si el usuario cancela la autenticación, pero sin contexto privado.
- Tras arrancar Jarvis sin una señal de desbloqueo, o después de 30 minutos, puede aparecer una
  única solicitud nativa en el siguiente turno reconocido; una autenticación correcta abre otra
  concesión acotada.
- Las rutas CLI y los clientes antiguos siguen funcionando, pero no reciben contexto privado porque
  no pueden declarar presencia verificada de manera implícita.
- La sesión bloqueada no se prueba bloqueando el Mac durante CI; el lease y los contratos se validan
  de forma determinista, y los eventos nativos se integran en la app.
