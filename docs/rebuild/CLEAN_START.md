# Inicio sobre un repositorio limpio

Decisión del dueño del 24-10-2026: retirar el árbol antiguo y empezar la variante
propia aquí, no mantener una segunda implementación junto al producto anterior.

## Punto de recuperación

- Revisión anterior a la limpieza: `c97ed8908e1233f282e67d08b41cd06cb071c431`.
- Producto previo a la investigación nueva: `4122a6e2e4ed5160abf2fc4480e752e6ab779197`.
- Se conservan `.git`, su historial y `LICENSE` original sin cambiar.
- Se retiraron 1.216 archivos versionados del árbol. Los inventarios de capacidades
  y la investigación permanecen en `docs/rebuild/` como documentación, no código activo.
- Los restos locales de las carpetas de código se movieron sin leer su contenido a
  `/private/tmp/aegis-clean-start-20261024-2ogf4mcy`, en un directorio privado. Esa
  copia temporal no sustituye al historial Git como punto de recuperación duradero.
- No se ejecutó `git clean`, no se reescribió historia ni se hizo force-push.

La app instalada y `~/Library/Application Support/Aegis` no se desinstalaron,
leyeron, borraron ni migraron por esta limpieza. El entorno `.venv` y los archivos
locales ajenos al árbol de código no se tratan como producto versionado.

## Qué contiene la base

- `src/aegis_core/__init__.py` y `runtime.py`: únicamente el núcleo **nuevo y propio**
  creado para esta reconstrucción. No son el daemon ni el grafo antiguos.
- `tests/`: contratos de la base, con proveedores/herramientas de fixture.
- `script/`: cualificación, activación de hooks e inventario de investigación.
- Metadatos Python, lock, hooks y CI específicos de la base limpia.
- Documentación de fases, app nativa pendiente e inventarios históricos congelados.

La clase del núcleo es `AgentRuntime`; las autorizaciones y resultados del prototipo
son contratos inertes, no permisos reales ni firmas IPC productivas.

## Historial e inventarios

`JARVIS_BACKEND_INVENTORY.md`, `JARVIS_NATIVE_INVENTORY.md` y
`JARVIS_DECLARATIONS.json` son snapshots históricos. Sus rutas y estados describen
la revisión indicada en ellos, no archivos ni herramientas actualmente disponibles.
Consultar el árbol histórico para recuperar una fuente concreta, sin reintroducir
el producto anterior como dependencia ni copiarlo en una carpeta legacy.

El script de investigación genera salidas con nombres distintos a esos snapshots
y exige ámbitos explícitos. No regenerar el inventario histórico sobre el núcleo
nuevo: se perdería la lista de capacidades que debe recuperar la fase B.

Las herramientas específicas se incorporarán después de cualificar la fase A.
La nueva app será nativa macOS en SwiftUI; todavía no está implementada o instalada.

## Cualificación de la base limpia

Los hooks y CI fueron reemplazados por controles de la base propia: imports y
dependencias, Ruff, compilación Python, suite de contratos, wheel/sdist y smoke
de instalación aislada sin red. No ejecutan ni acreditan pruebas Swift de una app
que aún no existe.

Una comprobación real rechazó código antiguo conservado en `build/lib`. Las
carpetas locales `build`, `dist` y `.build` también se movieron al respaldo privado.
Para evitar contaminación futura, el empaquetado genera un sdist y construye el
wheel desde ese sdist en un árbol temporal fresco. El guard contrasta las fuentes
del wheel con el árbol cualificado y rechaza recursos o dependencias ajenos.

La distribución inicial es `aegis-swarm 0.1.0a1`, sin dependencias productivas.
El paquete wheel actual contiene solo `aegis_core/__init__.py` y `runtime.py`.
Esto acredita un paquete Python propio, no una app de escritorio terminada.
