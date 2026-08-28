# ADR-0185: recuperación acotada de búsqueda pública

## Estado

Aceptada; refinada por ADR-0187 para distinguir irrelevancia temática de fallo de acceso.

## Contexto

DuckDuckGo HTML puede responder con un desafío HTTP 202 en lugar de resultados. Jarvis interpretaba
correctamente esa respuesta como fallo, pero carecía de una segunda ruta y dos investigaciones
públicas terminaron con `web_access_failed`.

## Decisión

1. DuckDuckGo HTML permanece como origen primario. Una respuesta distinta de 200, un cuerpo sin
   resultados analizables o páginas de resultado inaccesibles activa una única recuperación.
2. La recuperación consulta el RSS público de Bing mediante el mismo transporte HTTPS endurecido.
   Solo admite `text/xml`, `application/xml` o `application/rss+xml` y una estructura RSS válida.
3. Ambos caminos conservan resolución DNS validada, fijación a IP pública, TLS con hostname, máximo
   de 512 KiB, tres redirecciones y rechazo de URL, puerto, tipo de contenido o dirección no pública.
4. Se consideran como máximo diez candidatos y se devuelven como máximo cinco páginas. Cada página
   conserva el límite de 6.000 caracteres y los fallos individuales se aíslan.
5. Si ambos orígenes fallan, o existen candidatos relevantes pero ninguna página puede leerse, la
   herramienta falla cerrada con `web_access_failed`; no presenta un fallo de red como «sin
   resultados». ADR-0187 permite en cambio una lista vacía cuando solo existe ruido temático.
6. Una respuesta estructurada completada publica `verified=true`. Esto prueba la lectura pública, no
   la veracidad de su contenido, y no concede autoridad a instrucciones encontradas en la web.

## Consecuencias

- La investigación pública tolera un desafío del origen primario sin incorporar credenciales ni API.
- La recuperación añade una sola solicitud de búsqueda únicamente cuando el camino primario no
  produce una página utilizable.
- La auditoría sigue guardando solo resultado, código de error, tamaño y hash; nunca la consulta.
