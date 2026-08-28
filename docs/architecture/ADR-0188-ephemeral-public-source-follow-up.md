# ADR-0188: seguimiento efímero de fuentes públicas

## Estado

Aceptada.

## Contexto

La investigación web exacta presenta fuentes numeradas con dominio, título y extracto, pero la voz
no debe leer URLs largas y el HUD no debe convertirse en un chat. Volver a pedir al modelo que
interprete «abre la primera fuente» añadiría latencia y permitiría que invente o sustituya el
destino recuperado.

## Decisión

1. Después de un único resultado `web_research` exitoso, marcado `source=public_https` y
   `verified=true`, el gestor extrae como máximo tres URLs HTTPS estructuralmente válidas.
2. Las URLs viven solo en memoria de proceso durante cinco minutos y se aíslan por conversación.
   Una investigación posterior válida sin resultados las elimina; cerrar el daemon también.
3. Solo una gramática local exacta en español o inglés resuelve primera, segunda o tercera fuente.
   Si no existe la posición, Jarvis responde localmente y no consulta ningún modelo.
4. El contexto se inyecta únicamente para ese seguimiento exacto. Antes se eliminan las claves
   reservadas proporcionadas por el cliente, de modo que no pueda falsificar una URL recordada.
5. La llamada usa la base `local_context_reference`, distinta de una URL pronunciada literalmente.
   Por ello `browser_open_url` exige confirmación visible y ligada al destino exacto antes de abrir.
6. La ejecución vuelve a validar DNS, IP pública, TLS, puerto y ausencia de credenciales o fragmento.
   «Verificada» describe el transporte y el contrato de recuperación, no la veracidad editorial.

## Consecuencias

- El seguimiento no invoca Apple Intelligence ni NVIDIA y su latencia es local.
- La voz puede referirse naturalmente a una lista numerada sin pronunciar direcciones.
- Una página recuperada nunca hereda el bypass reservado a una URL dictada explícitamente.
- No se añade almacenamiento, dependencia, proceso de limpieza ni superficie persistente nueva.
