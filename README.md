# Aegis Swarm

Núcleo local, auditable y *voice-first* para un asistente táctico multiagente en macOS Apple Silicon.

Esta primera vertical contiene:

- contratos tipados para solicitudes, rutas y respuestas;
- registro central de modelos NVIDIA NIM;
- cliente HTTP asíncrono compatible con los endpoints NIM;
- recuperación de la credencial desde macOS Keychain;
- grafo LangGraph mínimo con escalamiento por especialidad;
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
