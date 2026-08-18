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
`aegis verify-audit <ruta>` comprueba permisos, secuencia y cadena hash del registro local.

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
