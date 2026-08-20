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
   Para visión, `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning` es el primario verificado con un
   PNG RGB sintético; `meta/muse-glimmer-30b` permanece como fallback porque no respondió dentro del
   timeout operativo durante la verificación del 20 de agosto de 2026.
8. El Tool Broker mantendrá un registro inmutable, capacidades por rol, argumentos Pydantic con
   `extra="forbid"`, alcances explícitos y confirmaciones temporales vinculadas al contenido de la
   llamada.
9. Solo las herramientas de lectura con ejecutor explícito pueden operar automáticamente. Las
   lecturas de archivos se resuelven desde un descriptor del workspace, rechazan enlaces simbólicos
   y aceptan únicamente archivos regulares con límites de bytes.
10. La auditoría será JSONL con permisos `0600`, bloqueo entre procesos y una cadena SHA-256. Se
    registrarán hashes y metadatos, nunca el contenido de las herramientas.
11. Las confirmaciones de alto riesgo serán efímeras y de un solo uso. Su digest incluirá `call_id`,
    rol, herramienta y argumentos; el consumo será atómico, con un TTL máximo de cinco minutos. El
    reinicio del daemon invalidará todas las aprobaciones pendientes.

## Consecuencias

- El núcleo es testeable sin red ni credenciales.
- Cambiar de proveedor o modelo no obliga a reescribir el grafo.
- La autonomía queda acotada por políticas locales y auditable.
- La primera vertical no ejecuta comandos de terminal ni escaneos de red.
