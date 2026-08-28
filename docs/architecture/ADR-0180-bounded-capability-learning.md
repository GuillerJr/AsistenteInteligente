# ADR-0180: aprendizaje acotado de capacidades ausentes

## Estado

Aceptada.

## Contexto

Jarvis debe poder admitir que aún no posee una capacidad, investigar cómo implementarla y evitar
repetir la misma búsqueda. Autoinstalar código, copiar instrucciones web o ampliar permisos sería
auto-modificación no auditable y convertiría una fuente no confiable en autoridad.

## Decisión

1. Si una orden operativa no coincide con herramienta ni Skill, LangGraph marca una brecha y ofrece
   al planner únicamente `web_research` con `tool_choice=required`.
2. La investigación usa una consulta no personal, hasta tres fuentes HTTPS públicas y prioriza
   documentación oficial. Los resultados son datos no confiables y nunca instrucciones.
3. Jarvis guarda objetivo normalizado, términos, fechas, ocurrencias y evidencia durante 30 días.
   El almacén privado admite 128 registros, 16 KiB por registro, permisos `0700/0600`, escritura
   atómica y verificación SHA-256 al cargar.
4. Una brecha equivalente reutiliza la evidencia solo con el cerebro local. NVIDIA recibe como
   máximo que existe una brecha, nunca el registro persistente. Una voz no verificada no lee ni
   escribe esta memoria.
5. El resultado debe declarar que la operación no se ejecutó y que no existe aún un ejecutor. El
   aprendizaje no instala paquetes, escribe código, concede TCC, crea herramientas ni evita el Tool
   Broker.
6. `capabilities.status` expone solo contadores. El CLI permite listar metadatos acotados y olvidar
   un identificador completo; no imprime extractos en el estado del daemon.

## Consecuencias

- Jarvis transforma un «no sé» repetido en una ruta investigada y reutilizable sin autoejecución.
- Convertir esa evidencia en capacidad real exige una implementación nativa, Skill o Capability Pack
  revisado, firmado y sujeto a la política existente.
- Si la evidencia caduca, está alterada, contiene secretos o el almacén falla, se ignora sin impedir
  que el turno principal termine.
