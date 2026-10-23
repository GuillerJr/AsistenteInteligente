# ADR-0235: inferencia seleccionada y primera conexión Codex con ChatGPT

Fecha: 2026-10-23. Estado: autorizado expresamente por el dueño.

## Contexto y decisión

El dueño retiró la exclusividad de NVIDIA NIM y eligió empezar por Codex con su
cuenta de ChatGPT. Esta decisión sustituye la política de inferencia de ADR-0234.
Ollama, OpenAI API y Anthropic son ampliaciones posteriores, no conexiones activas
ni respaldos automáticos de esta primera migración.

El daemon usa el cliente oficial Codex App Server, con estado propio y un espacio
vacío de trabajo. El inicio de sesión lo completa el dueño en el navegador. Jarvis
no lee ni importa el archivo `auth.json` ni el historial personal de otras apps.
Los modelos y sus capacidades se obtienen del catálogo disponible para la cuenta.

Codex propone texto y llamadas estructuradas; el broker de Jarvis sigue validando,
pidiendo aprobación y ejecutando herramientas. Las acciones nativas de Codex no
pueden sustituirlo. El cliente usa un perfil restringido, sin escritura ni red para
comandos, y rechaza solicitudes de ejecución o ampliación de permisos. La conexión
falla si no puede acreditar el perfil esperado; no degrada a acceso completo.

Los embeddings pasan al encoder local de Apple con su propia identidad de índice.
No se reutilizan ni proyectan los vectores NVIDIA. La voz y las notas de audio de
Telegram se declaran sin configurar mientras no tengan una alternativa operativa;
no se conserva una conexión NIM oculta ni se bloquea por ello el chat.

## Amenazas y controles

- Credenciales e historial: estado separado, inicio de sesión oficial, IPC solo de
  la app firmada; las respuestas públicas contienen estados, nunca tokens o correo.
- Inyección: los datos visuales y resultados siguen siendo contexto no fiable.
  Una propuesta pasa las mismas restricciones y comprobaciones del broker.
- Aprobaciones: las nuevas solicitudes muestran el destino Codex (OpenAI). Los
  permisos persistentes de apps se separan de los concedidos bajo la conexión NIM.
- Ejecución paralela: configuración generacional en la interfaz, cancelación de
  operaciones, límites temporales y rechazo de respuestas incompletas.
- Cambio de proveedor: no hay respaldo silencioso ni importación de credenciales.

## Validación y límites

Regresiones del cliente JSON-RPC, autenticación, catálogo, selección persistente,
controlador, broker, memoria, estado y SwiftUI. Las pruebas de transporte simulado
no acreditan el inicio de sesión del dueño ni la escritura real de TextEdit.
Ambos recorridos deben verificarse con la app firmada antes de cerrar P1-5/R2-3.

Referencia del protocolo: [Codex App Server](https://learn.chatgpt.com/docs/app-server).
