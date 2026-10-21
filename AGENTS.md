# Instrucciones para agentes (Codex, Claude Code)

Jarvis es un asistente personal para macOS Apple Silicon. Tiene dos partes:

- un daemon Python en `src/aegis_core`;
- una app SwiftUI de barra de menús en `native/AegisAudio`.

El nombre técnico heredado `aegis` se conserva en paquetes, rutas, Keychain e identificadores de bundle. **No lo renombres.**

La lista de trabajo pendiente para la 1.0 está en [`docs/V1_PENDIENTES.md`](docs/V1_PENDIENTES.md).

## Forma de trabajar

- **Rama:** trabaja solo en `main`, sin ramas ni PRs. Después de cada cambio terminado, haz commit y `git push` a `main`.
- **Pruebas:** ejecútalas tú. Si algo falla, corrígelo, reinstala y vuelve a probar hasta que funcione.
- **Interfaz:** está en español. Los textos nuevos también van en español, con el tono de los existentes.
- **Inferencia:** usa NVIDIA en exclusiva (`inference_nvidia_only=True`). No añadas proveedores ni respaldos locales. El endpoint de NVIDIA es gratuito, así que no hace falta advertir de cuotas.
- **Inspiración externa:** de ChatGPT, Codex y Claude toma solo ideas. Nunca copies su código ni sus recursos.
- **Lo que hace el dueño:**
  - escribe las contraseñas y la del llavero;
  - concede los permisos de privacidad (TCC);
  - aprueba las acciones.

  Nunca los hagas por él.
- **Archivos privados:** no leas `auth.json` ni el historial personal del dueño.

## Comandos

```bash
.venv/bin/ruff check src tests                        # lint (líneas de 100 caracteres)
PYTHONPATH=src .venv/bin/python -m pytest -p no:warnings
./script/test_native.sh                               # pruebas Swift
./script/menu_bar_service.sh install                  # compila, firma e instala ~/Applications/Jarvis.app
```

- **Entorno:** `.venv` apunta a `.venv.nosync` porque el repositorio vive en iCloud. Ignora los duplicados `* 2.*`.
- **Hooks:** se activan con `./script/setup_git_gates.sh`.
  - El **pre-commit** pasa ruff y pytest.
  - El **pre-push** compila y prueba **el árbol de trabajo**, no el commit. Haz commit de todo antes de `git push`, y no edites archivos ni lances `install` mientras corre.
  - El pre-push necesita Xcode fuera del sandbox.

## Reglas técnicas que ya han causado fallos

- **Swift:**
  - No uses literales de expresión regular (`/…/`): el analizador del pre-commit los rechaza. Usa análisis manual o `NSRegularExpression`.
  - Las llamadas de `LocalIPCClient` tienen un tiempo máximo de **30 s**. Si un método del daemon tarda más, ajusta `handler_timeout_overrides` en `runtime/daemon.py` por debajo de ese límite.
  - El ejecutable `Jarvis` tiene un presupuesto de tamaño (25 MiB tras `strip`) en `script/build_and_run.sh` y `script/rc3_rc8_gate.sh`. Comprueba el tamaño si añades código a la app.
- **IPC firmada:** solo la app firmada habla con el daemon. Un `IpcClient` de Python recibe `peer_not_trusted`, así que las pruebas en vivo se hacen desde la interfaz de la app.
- **Seguridad:**
  - Las herramientas se declaran como `ToolDefinition`; las que cambian algo llevan `requires_confirmation`.
  - Las aprobaciones nuevas necesitan su propio método firmado en `approval_signing.py` y en `ApprovalSigner.swift`.
  - Los metadatos que pone el daemon (`plan_mode`, `project_context`, conversación temporal) se eliminan si llegan del cliente.
- **Datos del dueño:** viven en `~/Library/Application Support/Aegis`. Los archivos privados se escriben con `write_private_json`.
