# ADR-0186: evidencia web pública determinista

## Estado

Aceptada.

## Contexto

Una investigación exacta ya devuelve consulta, título, URL, extracto y contenido bajo un contrato
acotado. Enviar esos 9–20 KiB a Foundation Models añadía hasta cuatro segundos antes del primer
fragmento y podía terminar en `local/privacy-fallback`, aunque la evidencia ya estuviera disponible.

## Decisión

1. Las órdenes exactas `Busca…`, `Investiga…` y `Lee https://…` se presentan mediante un formateador
   local determinista; no invocan Apple Foundation Models ni NVIDIA para sintetizar la lectura.
2. El resultado debe corresponder a la llamada directa, contener exactamente las claves previstas,
   respetar el máximo solicitado y usar URL HTTPS sin credenciales, fragmento ni puerto no estándar.
3. Se muestran dominio, título y un extracto normalizado de hasta 280 caracteres por fuente. Nunca se
   incluyen instrucciones de la página en prompts ni se interpreta su intención.
4. Un contrato inconsistente produce una respuesta local fija y no activa otro modelo.
5. Lecturas web elegidas por planificación abierta y análisis de código o ciberseguridad conservan
   sus rutas de razonamiento; esta optimización solo cubre la gramática local exacta.
6. Todo `model_id` con prefijo `local/` se contabiliza como determinista. Solo
   `apple/system-language-model` pertenece al cerebro local generativo; ningún fallback fijo se
   atribuye a NVIDIA. La métrica normaliza también registros históricos al leerlos, sin modificar
   la evidencia persistida.
7. Hasta tres páginas candidatas se leen en paralelo con trabajadores estándar acotados; cada
   solicitud conserva validación DNS, IP pública, TLS, redirecciones y tamaño. La salida recupera el
   orden de las fuentes, no el orden de terminación.
8. La búsqueda primaria dispone de dos segundos y el respaldo RSS de cuatro. Las páginas conservan
   ocho segundos. El transporte HTTPS fijado aplica el presupuesto al socket real; no se limita a
   configurar una capa superior que ya recibió la respuesta completa.

## Consecuencias

- El primer fragmento llega al terminar la lectura pública, sin esperar otra inferencia.
- La respuesta mantiene atribución de fuente y una superficie de prompt injection menor.
- El formateador no afirma haber corroborado la veracidad de una página; presenta evidencia pública.
- Una fuente lenta ya no bloquea secuencialmente las otras dos y no se añade una dependencia.

Una observación real posterior al despliegue completó una investigación de tres fuentes en 2.092 ms,
frente a 6.875 ms del camino anterior que esperaba síntesis local. Es una muestra operativa de esa
red y ese instante, no un benchmark ni una garantía de latencia externa.
