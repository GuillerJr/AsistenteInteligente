# ADR-0001: núcleo local y frontera de ejecución

- Estado: aceptado
- Fecha: 2026-08-18

## Contexto

El asistente se ejecutará en un MacBook Air M5 y consumirá modelos NVIDIA NIM mediante API. Los modelos pueden proponer decisiones y herramientas, pero no deben poseer autoridad directa sobre el sistema operativo.

## Decisión

1. El orquestador será un proceso Python nativo `arm64` basado en LangGraph.
2. La interfaz futura será Tauri/Three.js y se comunicará con el daemon por Unix domain socket.
3. Todo acceso externo se abstraerá detrás de puertos propios; LangChain/LangGraph no será la capa de transporte.
4. El Tool Broker determinista será la única frontera capaz de ejecutar herramientas.
5. Las credenciales se recuperarán de macOS Keychain y jamás se registrarán.
6. Las respuestas de routing se validarán con Pydantic antes de modificar el estado del grafo.
7. Los endpoints gratuitos de NVIDIA se tratarán como infraestructura de desarrollo sin SLA: timeout, throttling y deprecación son estados esperados.
8. El Tool Broker mantendrá un registro inmutable, capacidades por rol, argumentos Pydantic con
   `extra="forbid"`, alcances explícitos y confirmaciones temporales vinculadas al contenido de la
   llamada.

## Consecuencias

- El núcleo es testeable sin red ni credenciales.
- Cambiar de proveedor o modelo no obliga a reescribir el grafo.
- La autonomía queda acotada por políticas locales y auditable.
- La primera vertical no ejecuta comandos de terminal ni escaneos de red.
