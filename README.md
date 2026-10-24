# Jarvis

Reconstrucción propia en este repositorio, informada por el estudio de OpenClaw
con REA. El producto se llama **Jarvis**; los nombres técnicos `jarvis_core` y
`jarvis-core` se conservan. El clon externo es una referencia de lectura y no se
modifica, importa ni ejecuta como parte de Jarvis.

La **fase A** es construir y comprobar la variante base completa. Las mejoras
propias y herramientas adicionales del Jarvis anterior corresponden a una fase B
posterior. La base incluye las herramientas de OpenClaw: no basta un chat o un
núcleo experimental para dar por terminada esta fase.

## Estado real

Está en construcción. El primer recorrido de desarrollo conecta sesiones SQLite,
ejecución, eventos, IPC Unix autenticado y el cliente oficial Codex con ChatGPT.
La app SwiftUI usa una ventana propia; no requiere buscar una pestaña del navegador.
Las credenciales, conversaciones y app instaladas del producto anterior no se importan.

Esta entrega todavía no es la variante completa ni un sustituto cualificado de la
app instalada. El cliente inicial de Codex ofrece chat de texto: sus herramientas
están desactivadas y sus solicitudes de ejecución se rechazan. La disponibilidad
de una cuenta y una respuesta real requieren iniciar sesión mediante el cliente
oficial; un peer sintético no acredita inferencia.

El alcance y los criterios de aceptación están en la
[matriz de paridad](docs/rebuild/OPENCLAW_BASELINE_PARITY.md). Los inventarios y las
pruebas no equivalen a una revisión exhaustiva de la referencia.

## Comprobar

```bash
uv sync --locked --group dev
./script/setup_git_gates.sh --install
.venv/bin/python script/check_clean_base.py
.venv/bin/ruff check src tests script
PYTHONPATH=src .venv/bin/python -m pytest -p no:warnings
./script/test_desktop.sh
```

Las pruebas de IPC necesitan poder crear sockets Unix privados. El servicio de
prueba exige rutas absolutas: `python -m jarvis_core.service --state RUTA_NUEVA
--codex RUTA_CLIENTE_OFICIAL`, con `PYTHONPATH=src`. Su directorio debe ser privado
y tener una ruta corta por el límite de sockets Unix de macOS.

Este comando no lee credenciales ni historial de otra instalación. El inicio de
sesión se solicita desde la app; el dueño lo completa en la página oficial.
El paquete Python no incluye el ejecutable oficial Codex ni un intérprete embebido.

## Registros

- [Fases y arquitectura](docs/rebuild/README.md).
- [Paridad funcional de la referencia](docs/rebuild/OPENCLAW_BASELINE_PARITY.md).
- [Verificación del recorrido inicial](docs/rebuild/BASELINE_VERTICAL_SLICE.md).
- [Limpieza y recuperación del producto anterior](docs/rebuild/CLEAN_START.md).
- [Pendientes](docs/V1_PENDIENTES.md).
- [Inventario histórico backend](docs/rebuild/JARVIS_BACKEND_INVENTORY.md).
- [Inventario histórico nativo](docs/rebuild/JARVIS_NATIVE_INVENTORY.md).
- [Cobertura REA parcial](docs/rebuild/REA_COVERAGE.json).

Los inventarios históricos se conservan congelados. No describen funciones
disponibles en la reconstrucción ni autorizan adelantar la fase B.
