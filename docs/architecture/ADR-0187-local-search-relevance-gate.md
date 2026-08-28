# ADR-0187: compuerta local de relevancia web

## Estado

Aceptada.

## Contexto

Una recuperación RSS válida puede devolver noticias que coinciden con una palabra genérica pero no
con el tema solicitado. La red, TLS y el contrato pueden estar intactos y aun así producir evidencia
inútil. Entregar ese ruido con apariencia de investigación reduce la confianza en Jarvis.

## Decisión

1. La consulta se normaliza sin acentos y se tokeniza localmente. Conectores y términos bilingües
   genéricos como «noticias», «actual», «reporte» o «seguridad» no deciden relevancia.
2. Un candidato debe cubrir un término distintivo si existe uno, o dos cuando existen dos o más.
   La cobertura se busca en título, dominio/ruta y resumen sin embeddings ni inferencia. Los
   parámetros de consulta de una URL no cuentan porque el buscador puede copiarlos mecánicamente.
3. Título y URL pesan más que el resumen para ordenar candidatos. Los empates conservan el orden del
   buscador y solo se descargan los mejores dentro del límite solicitado.
4. Después de una redirección y lectura HTTPS, la página final debe superar la misma cobertura usando
   su URL, título y contenido. Un resumen relevante no puede legitimar una página final distinta.
5. Candidatos relevantes pero inaccesibles conservan `web_access_failed`. Candidatos legibles pero
   temáticamente ajenos producen una lista vacía y la respuesta determinista de «no encontré».
6. La compuerta solo mide relación léxica. No certifica verdad, autoridad, actualidad ni seguridad
   del contenido y no transforma texto web en instrucciones.

## Consecuencias

- Jarvis evita descargar páginas evidentemente ajenas y reduce latencia y exposición innecesarias.
- El comportamiento es reproducible, auditable y no añade dependencias ni llamadas de modelo.
- Una consulta demasiado genérica conserva el orden del buscador porque no existe un término
  distintivo suficiente para aplicar el filtro con honestidad.

Dos observaciones reales posteriores al despliegue completaron sin modelos: la consulta contaminada
sobre aplicaciones macOS devolvió una lista vacía verificada en 1.472 ms, mientras `NVIDIA NIM`
conservó tres fuentes oficiales en 1.727 ms. Son muestras operativas, no un benchmark ni una garantía
de la red externa.
