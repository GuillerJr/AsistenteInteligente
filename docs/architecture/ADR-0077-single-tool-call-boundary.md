# ADR-0077: Una sola llamada de herramienta por turno

- Estado: aceptado
- Fases: 1 y 4

## Decisión

Cada `AgentResult` admite como máximo un `ToolCall`. El parser NVIDIA rechaza una respuesta con más
de una llamada antes de construir contratos, crear autorizaciones, registrar auditoría o invocar un
ejecutor. LangGraph repite el límite para cubrir proveedores internos o pruebas que omitan la
validación de Pydantic.

El límite aplica tanto a herramientas de lectura como a mutaciones confirmadas. Una solicitud que
necesite varias acciones debe dividirse en turnos explícitos, conservando una decisión, un resultado
y, cuando corresponda, una confirmación exacta por turno.

## Consecuencias

- Una respuesta anómala del proveedor no multiplica accesos web, correo, calendario o archivos.
- El costo, la latencia, el payload del synthesizer y la auditoría quedan acotados por turno.
- No se añade cola, planificador de acciones, rollback ni protocolo de aprobación múltiple.
