# ADR-0177: Búsqueda visible de navegador confirmada

## Estado

Aceptada.

## Contexto

«Investiga seguridad en Apple Silicon» y «Busca seguridad en Apple Silicon en Safari» no expresan
la misma intención. La primera pide a Jarvis investigar y responder; la segunda pide ver resultados
en una aplicación concreta. Resolver ambas con planificación NVIDIA, captura de pantalla o control
visual aumentaría latencia, exposición de datos y superficie de fallo sin aportar autoridad.

Una consulta de búsqueda abandona el Mac. También puede contener por accidente una clave, token o
contraseña. Abrir un navegador es una mutación visible y debe conservar el consentimiento de un solo
uso, la política y la auditoría del resto del sistema.

## Decisión

1. Jarvis reconoce localmente formas exactas en español e inglés cuyo final identifica Safari,
   Chrome, Firefox o el navegador predeterminado. Se aceptan consultas naturales o entre comillas;
   se rechazan órdenes compuestas y navegadores desconocidos.
2. El compilador local produce un contrato estricto con solo `query` y `browser`. No recupera
   memoria, no consulta Apple Intelligence y no llama a NVIDIA.
3. El detector local de secretos rechaza material con apariencia de credencial antes del broker,
   de cualquier modelo y del ejecutor.
4. `browser_search` es una acción de control de aplicaciones de riesgo alto. La aprobación muestra
   la consulta, el navegador y que DuckDuckGo recibirá el texto. La autorización queda ligada a esos
   argumentos exactos y se consume una sola vez.
5. El ejecutor construye exclusivamente `https://duckduckgo.com/?q=...` mediante codificación URL.
   Ejecuta `/usr/bin/open` con una tupla fija de argumentos y, cuando aplica, un bundle ID de una
   lista cerrada. No usa shell, cookies, sesión web, captura ni puntero.
6. El resultado debe coincidir exactamente con la autorización y se presenta con una plantilla
   local. Un código exitoso de `/usr/bin/open` significa que macOS aceptó la solicitud; no prueba que
   el navegador cargó o que Jarvis leyó la página.

## Consecuencias

- La orden común evita dos inferencias, memoria y percepción visual.
- El usuario ve y controla la única divulgación externa antes de que ocurra.
- La búsqueda no obtiene acceso a una sesión autenticada ni amplía permisos del navegador.
- Si el contrato, la consulta, el navegador, la aprobación o la salida difieren, la acción falla de
  forma cerrada.
- Investigar, leer una URL, abrir una URL y buscar visiblemente permanecen capacidades separadas.
