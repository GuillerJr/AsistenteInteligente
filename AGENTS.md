# Instrucciones para agentes

Esta es la base limpia de una variante propia inspirada en el estudio de OpenClaw.
Se construye en este repositorio con código propio. OpenClaw es referencia de lectura
por partes con REA, no un fork, árbol vendorizado ni runtime del producto.

## Estado y fases

- Fase A: núcleo propio, sesiones, política, extensibilidad, estado, proveedor elegido
  y app nativa macOS en SwiftUI con ventana, Dock, barra de menús y atajo.
- Fase B posterior: herramientas específicas de Jarvis. Están inventariadas en
  `docs/rebuild/`; no implementarlas ni conectarlas antes de terminar la base A.
- Hoy existe un núcleo Python aislado con pruebas sintéticas. La nueva app de
  escritorio, el proveedor real y los transportes de producción siguen pendientes.
- El código del Jarvis anterior fue retirado del árbol de trabajo por petición
  expresa del dueño. Se conserva en Git, no en una carpeta de código heredado.

## Forma de trabajar

- Solo `main`, sin ramas ni PRs. Después de cada cambio terminado: commit y push.
- Ejecutar pruebas y corregir fallos. No cambiar archivos ni instalar mientras
  corre pre-push: verifica el árbol de trabajo completo y su identidad.
- Conservar los nombres técnicos `aegis`, la distribución `aegis-swarm` y los
  identificadores heredados que deban mantenerse. El producto se llama Jarvis.
- Interfaz y textos al usuario en español.
- Primera conexión elegida: Codex con ChatGPT, mediante cliente y acceso oficiales.
  No enviar inferencia, memoria ni audio a NVIDIA. Otros proveedores se incorporan
  solo por elección explícita, sin respaldos ocultos.
- De ChatGPT, Codex y Claude tomar solo ideas, nunca copiar código o recursos.
  La variante propia tampoco importa la implementación o recursos de OpenClaw.
- El dueño escribe contraseñas, concede permisos TCC y aprueba acciones reales.
  Nunca hacerlo por él. Los gates y herramientas de los tests son sintéticos.
- No leer `auth.json`, historial personal ni datos privados del dueño.
- No tocar la app instalada ni `~/Library/Application Support/Aegis` al limpiar código.
- Ignorar duplicados iCloud `* 2.*`. `.venv` puede apuntar a `.venv.nosync`.

## Comandos de la base actual

```bash
uv sync --locked --group dev
.venv/bin/ruff check src tests script
.venv/bin/python -m compileall -q src
PYTHONPATH=src .venv/bin/python -m pytest -p no:warnings
.venv/bin/python script/check_clean_base.py
./script/setup_git_gates.sh --check
AEGIS_PACKAGE_DIR="$(mktemp -d /private/tmp/aegis-base-package.XXXXXX)"
uv build --offline --sdist --out-dir "$AEGIS_PACKAGE_DIR"
uv build --offline --wheel "$AEGIS_PACKAGE_DIR"/*.tar.gz --out-dir "$AEGIS_PACKAGE_DIR"
.venv/bin/python script/check_clean_base.py \
  --wheel "$AEGIS_PACKAGE_DIR"/*.whl --sdist "$AEGIS_PACKAGE_DIR"/*.tar.gz --smoke
```

Los hooks califican la base Python y sus paquetes. No hay un instalador de app ni
pruebas Swift de la variante nueva todavía; no sustituirlos por comandos eliminados
ni afirmar que el empaquetado Python acredita una app macOS funcional.

## Garantías de diseño

- Una autoridad de autorización, ligada a operación, sesión, ejecución y argumentos.
- Cancelar entrega recibo; el siguiente run espera que termine la limpieza anterior.
- Texto de modelo y recibos de acciones son datos distintos. Un efecto incierto se
  conserva y no se repite automáticamente.
- Los adaptadores productivos necesitarán autenticación, firmas, límites, revocación
  y comprobación inmediatamente antes del efecto. El prototipo no acredita TCC.
- La investigación mantiene identidad, fuentes, hashes, Evidence IDs e incógnitas.
  Nunca presentar cobertura parcial o pruebas sintéticas como revisión exhaustiva.
- Los inventarios históricos están congelados. No regenerarlos sobre el árbol nuevo
  ni perder capacidades al simplificar la arquitectura.

Consultar `docs/rebuild/README.md`, `docs/rebuild/CLEAN_START.md` y
`docs/V1_PENDIENTES.md` antes de ampliar el alcance.
