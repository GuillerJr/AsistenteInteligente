# Jarvis — base propia

Base limpia para una variante propia inspirada en el análisis de OpenClaw.
El código anterior de Jarvis se retiró del árbol de trabajo; su historial y el
inventario de capacidades se conservan como referencia.

Primero se construye la **variante base independiente (fase A)**. Las herramientas
específicas de Jarvis se integrarán en una **fase B posterior**.

## Estado real

- Núcleo propio Python sin dependencias productivas externas: sesiones, ejecución,
  aprobación por operación, cancelación/asentamiento y recibos de efectos inciertos.
- Pruebas sintéticas con proveedores y herramientas de fixture.
- App nativa macOS en SwiftUI, proveedor real, persistencia y transporte autenticado:
  **pendientes de implementación y cualificación**.
- La conversación habitual se hará en una ventana propia con Dock, barra de menús
  y atajo. La nueva app no será una pestaña ni un panel web incrustado.

No es una app lista para instalar. El prototipo no conecta cuentas, micrófono,
control del Mac ni herramientas de la versión anterior.

## Desarrollar y comprobar

```bash
uv sync --locked --group dev
./script/setup_git_gates.sh --install
.venv/bin/python script/check_clean_base.py
.venv/bin/ruff check src tests script
PYTHONPATH=src .venv/bin/python -m pytest -p no:warnings
AEGIS_PACKAGE_DIR="$(mktemp -d /private/tmp/aegis-base-package.XXXXXX)"
uv build --offline --sdist --out-dir "$AEGIS_PACKAGE_DIR"
uv build --offline --wheel "$AEGIS_PACKAGE_DIR"/*.tar.gz --out-dir "$AEGIS_PACKAGE_DIR"
.venv/bin/python script/check_clean_base.py \
  --wheel "$AEGIS_PACKAGE_DIR"/*.whl --sdist "$AEGIS_PACKAGE_DIR"/*.tar.gz --smoke
```

El entorno local `.venv` puede ser un enlace a `.venv.nosync` por iCloud.
La distribución técnica conserva `aegis-swarm` y el paquete conserva `aegis_core`.

## Documentación

- [Plan de la variante propia y fases](docs/rebuild/README.md).
- [Limpieza, punto de recuperación y límites](docs/rebuild/CLEAN_START.md).
- [Contrato de la app nativa](docs/rebuild/DESKTOP_APP.md).
- [Pendientes actuales](docs/V1_PENDIENTES.md).
- [Inventario histórico backend](docs/rebuild/JARVIS_BACKEND_INVENTORY.md).
- [Inventario histórico nativo](docs/rebuild/JARVIS_NATIVE_INVENTORY.md).
- [Cobertura REA, todavía parcial](docs/rebuild/REA_COVERAGE.json).

Los inventarios describen el producto anterior, no funciones disponibles en esta
base. La investigación de OpenClaw no importa su código al producto nuevo.
