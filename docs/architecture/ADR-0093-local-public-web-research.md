# ADR-0093: investigación web pública con planificación local

- Estado: aceptado
- Fases: 1, 2, 4 y 5

## Decisión

1. Una gramática exacta convierte `Busca …` o `Investiga …` en `web_research`, con un máximo fijo de
   tres resultados, sin ejecutar una inferencia de planificación.
2. `Lee https://…` se convierte en `web_fetch`, limitado a 8.000 caracteres. `Abre https://…` se
   convierte en `browser_open_url`, pero conserva la confirmación de un solo uso.
3. Investigación y lectura pasan por el broker, el ejecutor auditado y el cliente HTTPS público ya
   endurecido. No comparten cookies, credenciales ni sesiones con un navegador.
4. El resultado de solo lectura se sintetiza con Apple Foundation Models on-device. Si el helper
   falla antes del primer delta, se usa el fallback NVIDIA existente.
5. Órdenes compuestas, negadas, multimodales, forzadas a remoto o que no coincidan exactamente
   conservan el camino normal del enjambre.

## Motivo

Estas órdenes ya contienen la herramienta y límites necesarios. Pedir a NVIDIA que vuelva a
descubrirlos agrega latencia y una ronda remota sin aportar una decisión útil. En 500 ejecuciones
simuladas del grafo se observaron cero rondas NVIDIA, 500 síntesis locales y 2,11 ms promedio de
overhead interno. La medición sustituyó red y modelo por dobles controlados; no representa su
latencia real.

## Límites de seguridad

- Solo se permite HTTPS en el puerto estándar y hacia direcciones públicas validadas antes de cada
  conexión y redirección.
- Se rechazan credenciales en URL, redes privadas o locales, contenido binario, respuestas mayores
  de 512 KiB y más de tres redirecciones.
- La investigación usa DuckDuckGo HTML y lee hasta tres páginas, cada una con texto acotado.
- El contenido web sigue siendo no confiable y solo se entrega como dato al synthesizer; no puede
  ampliar el conjunto de herramientas autorizado.
- Abrir una URL es una acción visible separada. Nunca se ejecuta dentro del camino de lectura y
  requiere aprobación explícita.
- Si Apple Intelligence no está disponible, el fallback NVIDIA puede recibir el resultado público
  acotado para sintetizarlo.
