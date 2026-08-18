# Aegis Swarm

Núcleo local, auditable y *voice-first* para un asistente táctico multiagente en macOS Apple Silicon.

Esta primera vertical contiene:

- contratos tipados para solicitudes, rutas y respuestas;
- registro central de modelos NVIDIA NIM;
- cliente HTTP asíncrono compatible con los endpoints NIM;
- recuperación de la credencial desde macOS Keychain;
- grafo LangGraph mínimo con escalamiento por especialidad;
- Tool Broker con capacidades por agente y política de denegación por defecto;
- ejecutores locales de solo lectura con auditoría JSONL encadenada;
- daemon local autenticado mediante Unix Domain Socket;
- memoria persistente local con SQLite/FTS5 y aislamiento por namespace;
- pruebas sin llamadas reales a servicios externos.

## Seguridad

La API key no debe guardarse en el repositorio ni en archivos `.env`. El servicio de Keychain usado por defecto es `ai.aegis.nvidia-nim`, con la cuenta `default`.

## Entorno

El proyecto requiere Python 3.11, 3.12 o 3.13 nativo `arm64`. La configuración evita depender de contenedores o binarios x86 para el núcleo local.

```bash
uv sync --all-groups --no-editable --python /opt/homebrew/bin/python3.11 --cache-dir .uv-cache
uv run --no-sync pytest
uv run --no-sync aegis doctor
```

`aegis doctor` verifica arquitectura, configuración y presencia de la credencial sin imprimirla.
`aegis probe-nvidia` realiza una inferencia mínima y solo informa estado y modelo, nunca el secreto.
`aegis probe-nvidia-embedding` verifica el endpoint de embeddings con una frase sintética y solo
informa modelo y dimensiones; nunca imprime el vector ni la credencial.
`aegis verify-audit <ruta>` comprueba permisos, secuencia y cadena hash del registro local.

## Daemon local

```bash
uv run --no-sync aegis daemon
uv run --no-sync aegis daemon-status
```

El daemon escucha por defecto en `~/Library/Application Support/Aegis/aegis.sock`. El directorio y
el socket requieren permisos `0700` y `0600`, respectivamente. Cada conexión valida el UID del
proceso cliente mediante credenciales nativas de macOS; cada mensaje y respuesta se autentica además
con HMAC-SHA256 usando un secreto independiente guardado en Keychain bajo `ai.aegis.ipc-auth`.

El protocolo `1.0` limita cada frame a 64 KiB, acepta una solicitud por conexión y rechaza timestamps
fuera de ventana, nonces repetidos, métodos desconocidos y payloads inesperados. Expone `health`,
`runtime.info`, `swarm.submit`, `jobs.status` y `jobs.cancel`.

También expone `memory.put`, `memory.get`, `memory.search` y `memory.delete`. Estas operaciones pasan
por el mismo socket autenticado, validan esquemas estrictos y ejecutan el acceso SQLite fuera del
event loop.

Las solicitudes del enjambre se ejecutan como trabajos asíncronos en memoria con estados `queued`,
`running`, `completed`, `failed` y `cancelled`. La cola está acotada, elimina primero resultados
terminales antiguos y nunca devuelve detalles internos de excepciones. Los resultados públicos se
limitan a 24 KiB UTF-8 para respetar el framing. Al detener el daemon se cancelan todos los trabajos
activos; reiniciarlo no restaura trabajos anteriores.

## Memoria persistente

La base local se guarda por defecto en
`~/Library/Application Support/Aegis/memory.sqlite3`. Tanto el directorio como la base deben
pertenecer al usuario y tener permisos `0700` y `0600`; se rechazan enlaces simbólicos, archivos no
regulares, esquemas desconocidos y bases con una identidad de aplicación diferente.

Cada registro pertenece a un namespace explícito y se clasifica como `episodic`, `preference`,
`semantic` o `summary`. La recuperación textual usa FTS5 con consultas parametrizadas, resultados
acotados y extractos de hasta 768 bytes. La capacidad nunca provoca borrado automático: una base
llena rechaza nuevas escrituras. Los borrados usan `secure_delete` y exigen namespace e ID exactos.
Esta base no es un almacén de credenciales; patrones evidentes de claves se rechazan y los secretos
continúan residiendo exclusivamente en Keychain.

FTS5 constituye el primer nivel determinista de RAG local. El esquema v2 puede almacenar vectores
`float32` normalizados y combinar ranking léxico y semántico mediante Reciprocal Rank Fusion. La
búsqueda vectorial se limita por defecto a las 2.000 memorias indexadas más recientes para mantener
latencia y consumo de RAM previsibles en Apple Silicon.

Los embeddings remotos están desactivados por defecto. Para aceptar explícitamente que el contenido
indexado y las consultas se envíen al endpoint NVIDIA NIM, se configura:

```bash
export AEGIS_MEMORY_REMOTE_EMBEDDINGS_ENABLED=true
```

El modelo configurado es `nvidia/nemotron-3-embed-1b` y se consume mediante
`https://integrate.api.nvidia.com/v1/embeddings`; no se descarga ningún modelo. Si el endpoint falla
o aplica rate limiting, la recuperación continúa con FTS5 local. LangGraph consulta siempre el
namespace fijo `user.default` —configurable por el operador, no por el prompt— después del routing,
y recibe un máximo de 4 KiB de extractos marcados explícitamente como datos no confiables.

## Frontera de herramientas

Los modelos reciben únicamente los esquemas compatibles con su rol. Cada llamada propuesta se
valida con argumentos estrictos, alcance local y nivel de riesgo. Las operaciones de riesgo alto o
crítico requieren una confirmación de un solo uso ligada al identificador, agente, herramienta y
argumentos exactos de la llamada. Las aprobaciones vencen en un máximo de cinco minutos, se consumen
atómicamente y se invalidan al reiniciar el proceso. El grafo produce veredictos `allow`,
`require_confirmation` o `deny`; todavía no existe ningún ejecutor de red o terminal.

Los ejecutores habilitados se limitan a metadatos no secretos del runtime y archivos UTF-8 regulares
dentro del workspace. La lectura usa descriptores relativos y no sigue enlaces simbólicos. El log de
auditoría conserva decisiones, códigos de resultado, tamaño y SHA-256 de la salida; no almacena el
contenido producido por una herramienta. El daemon deberá inyectar un `HashChainAuditLog` apuntando
a su directorio privado de datos.
