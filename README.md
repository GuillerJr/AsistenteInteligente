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
app instalada. Codex recibe el catálogo propio de Jarvis: archivos, memoria local
y servidores MCP stdio configurados por el dueño. Las mutaciones pasan por una
única autoridad de aprobación por operación; los recibos se muestran separados del
texto del modelo. Las herramientas incorporadas de Codex continúan desactivadas.
La continuación añade procesos aislados, perfiles de agente, skills locales,
tareas programadas con activación explícita y ramas de conversación sin acciones.
El dueño completó el acceso oficial y se verificaron inferencia, archivos, memoria
tras reinicio y una llamada MCP de prueba. Un peer sintético no acredita por sí
solo una conexión o una acción real; ver alcance y límites en los registros.

El alcance y los criterios de aceptación están en la
[matriz de paridad](docs/rebuild/OPENCLAW_BASELINE_PARITY.md). Los inventarios y las
pruebas no equivalen a una revisión exhaustiva de la referencia.

## Comprobar

```bash
uv sync --locked --group dev
.venv/bin/python -B script/check_clean_base.py
.venv/bin/ruff check --no-cache src tests script
./script/setup_git_gates.sh --check
```

Estos comandos locales comprueban fuentes y configuración. Las suites completas
Python y Swift, los paquetes, el smoke de instalación y el bundle se validan en
[GitHub Actions](.github/workflows/ci.yml) para el commit exacto. No ejecutar
directamente `pytest`, `swift test` ni compilaciones completas en el Mac del dueño.
Una selección local focal necesita supervisor, cuota, plazo y recibo según los
[límites de recursos](docs/rebuild/TEST_RESOURCE_LIMITS.md).

Las pruebas de IPC necesitan poder crear sockets Unix privados. El servicio de
prueba exige rutas absolutas: `python -m jarvis_core.service --state RUTA_NUEVA
--codex RUTA_CLIENTE_OFICIAL`, con `PYTHONPATH=src`. Su directorio debe ser privado
y tener una ruta corta por el límite de sockets Unix de macOS.

Este comando no lee credenciales ni historial de otra instalación. El inicio de
sesión se solicita desde la app; el dueño lo completa en la página oficial.
El paquete Python no incluye el ejecutable oficial Codex ni un intérprete embebido.

La [distribución autocontenida y residencia](docs/rebuild/PHASE_A_DISTRIBUTION_RESIDENT.md)
se cualifican por separado. La [CI exacta de `29b730c`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/41069453178)
aprobó la app con Python, dependencias, Codex y MiniLM incluidos, su traslado y el
cierre del servicio por pérdida del host. El pin del manifiesto queda en el
Info.plist firmado. El [registro](docs/rebuild/PHASE_A_DISTRIBUTION_RESIDENT_QUALIFICATION.json)
conserva también el recorrido nativo aprobado: respuesta real, cierre de ventanas, reapertura y salida sin procesos retenidos. El modo de desarrollo
anterior conserva sus rutas explícitas. Los programas de servidores MCP externos
pueden requerir su propio runtime; no se incluyen por estar configurados.
La firma ad hoc no equivale a Developer ID ni notarización pública. No se ha
registrado un Login Item real ni probado macOS 14; la actualización pública y el
cierre global de fase A siguen pendientes, y B continúa aplazada.

## Registros

- [Fases y arquitectura](docs/rebuild/README.md).
- [Paridad funcional de la referencia](docs/rebuild/OPENCLAW_BASELINE_PARITY.md).
- [Verificación del recorrido inicial](docs/rebuild/BASELINE_VERTICAL_SLICE.md).
- [Herramientas, memoria y MCP conectados](docs/rebuild/PHASE_A_TOOLS_MEMORY_MCP.md).
- [Procesos, agentes, skills y programador](docs/rebuild/PHASE_A_MANAGEMENT.md).
- [Plataformas y conexiones elegidas](docs/rebuild/PHASE_A_TARGETS.md).
- [Verificación de este avance](docs/rebuild/PHASE_A_QUALIFICATION.json).
- [Limpieza y recuperación del producto anterior](docs/rebuild/CLEAN_START.md).
- [Pendientes](docs/V1_PENDIENTES.md).
- [Inventario histórico backend](docs/rebuild/JARVIS_BACKEND_INVENTORY.md).
- [Inventario histórico nativo](docs/rebuild/JARVIS_NATIVE_INVENTORY.md).
- [Cobertura REA parcial](docs/rebuild/REA_COVERAGE.json).

Los inventarios históricos se conservan congelados. No describen funciones
disponibles en la reconstrucción ni autorizan adelantar la fase B.
