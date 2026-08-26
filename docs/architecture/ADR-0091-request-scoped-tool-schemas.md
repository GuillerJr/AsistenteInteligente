# ADR-0091: esquemas de herramienta limitados por solicitud

- Estado: aceptado
- Fases: 1 y 4

## Decisión

1. El enrutador clasifica localmente una intención operativa en dominios explícitos: web, Mail,
   Calendario, archivo, red, diagnóstico, aplicación, atajo o control visual.
2. NVIDIA recibe solo los esquemas cuyos nombres corresponden a esos dominios y que además están
   permitidos para el rol. Si la solicitud es operativa pero el dominio queda vacío, se conserva el
   catálogo completo del rol como fallback de capacidad.
3. La instrucción del sistema enumera únicamente los nombres realmente enviados. Las restricciones
   adicionales de `computer_use` solo se incorporan cuando esa herramienta está disponible.
4. El broker recibe el mismo conjunto de nombres durante la primera autorización. Una función
   conocida pero no ofrecida se deniega con `tool_not_offered`, antes de validar argumentos,
   confirmar o ejecutar.
5. Las acciones deterministas se vinculan exclusivamente al nombre exacto construido localmente.
   La aprobación posterior consume el digest ya autorizado y no admite sustitución.

## Motivo

El planner enviaba 11 esquemas y 5.641 bytes incluso para revisar correo; seguridad enviaba 6 y
2.628 bytes para leer un archivo. En mediciones locales, limitar el caso común a un esquema redujo
entre 81,1 % y 91,8 % el payload de definiciones. Menos alternativas reducen tokens, latencia y
selecciones erróneas sin añadir un modelo de routing.

## Límites

- La selección de esquemas no autoriza la herramienta; rol, capacidad, argumentos, riesgo,
  confirmación y auditoría siguen vigentes.
- El fallback completo solo se usa cuando ya existe intención operativa pero el dominio no es
  inequívoco.
- Una herramienta nueva requiere vocabulario explícito y pruebas de ámbito y denegación.
- Las reducciones miden bytes JSON locales; la latencia final depende también del endpoint NVIDIA.
