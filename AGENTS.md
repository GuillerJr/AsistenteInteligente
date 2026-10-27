# Instrucciones para agentes

Esta es la base limpia de una variante propia inspirada en el estudio de OpenClaw.
Se construye en este repositorio con código propio. OpenClaw es referencia de lectura
por partes con REA, no un fork, árbol vendorizado ni runtime del producto.

## Estado y fases

- Fase A: variante completa funcional de OpenClaw bajo el nombre Jarvis, con código
  propio y app nativa SwiftUI. Primero acreditar la base, antes de mejoras propias.
  La matriz `docs/rebuild/OPENCLAW_BASELINE_PARITY.json` fija el alcance y evidencias.
  No reducir esta fase a un kernel o diferir herramientas propias de OpenClaw a B.
- Fase B posterior: mejoras y herramientas adicionales del Jarvis anterior. Inventariadas en
  `docs/rebuild/`; no implementarlas ni conectarlas antes de terminar la base A.
- Hay un primer recorrido de desarrollo: núcleo Python, SQLite, IPC local, cliente
  oficial Codex y app SwiftUI. No es todavía la variante completa ni una versión
  cualificada para reemplazar la instalación anterior. Ver estados y pruebas en docs.
- El código del Jarvis anterior fue retirado del árbol de trabajo por petición
  expresa del dueño. Se conserva en Git, no en una carpeta de código heredado.

## Forma de trabajar

- Solo `main`, sin ramas ni PRs. Después de cada cambio terminado: commit y push.
- Ejecutar pruebas y corregir fallos. No cambiar archivos ni instalar mientras
  corre pre-push: verifica el árbol de trabajo completo y su identidad.
- Usar Jarvis en carpetas, archivos, código, paquetes, identificadores y scripts.
  El núcleo es `jarvis_core` y la distribución `jarvis-core`. Esta orden del dueño
  reemplaza la anterior obligación de conservar el nombre técnico del producto previo.
- Interfaz y textos al usuario en español.
- Primera conexión elegida: Codex con ChatGPT, mediante cliente y acceso oficiales.
  No enviar inferencia, memoria ni audio a NVIDIA. Otros proveedores se incorporan
  solo por elección explícita, sin respaldos ocultos.
- De ChatGPT, Codex y Claude tomar solo ideas, nunca copiar código o recursos.
  La variante propia tampoco importa la implementación o recursos de OpenClaw.
- El dueño escribe contraseñas, concede permisos TCC y aprueba acciones reales.
  Nunca hacerlo por él. Los gates y herramientas de los tests son sintéticos.
- No leer `auth.json`, historial personal ni datos privados del dueño.
- No tocar la app instalada ni los perfiles y datos anteriores del dueño al limpiar código.
- Ignorar duplicados iCloud `* 2.*`. `.venv` puede apuntar a `.venv.nosync`.

## Comandos de la base actual

Desde el incidente de almacenamiento del 27-10-2026, las suites completas se
ejecutan en GitHub Actions (Ubuntu y macOS), por elección expresa del dueño.
En el Mac sólo se admiten selecciones focales supervisadas, con cuota, plazo y
recibo. No ejecutar directamente `pytest`, `swift test` ni repetir una suite
completa desde hooks. No eliminar perfiles, originales ni custodia incierta para
liberar espacio. Ver `docs/rebuild/TEST_RESOURCE_LIMITS.md`.

```bash
uv sync --locked --group dev
.venv/bin/ruff check --no-cache src tests script
.venv/bin/python script/check_clean_base.py
./script/setup_git_gates.sh --check
.venv/bin/python script/test_resource_guard.py run --profile local --kind python \
  --selection tests/test_test_resource_guard.py --budget-mib 768 --deadline 180 \
  -- .venv/bin/python -m pytest tests/test_test_resource_guard.py
```

Los hooks locales comprueban fuentes e identidad del árbol; indican expresamente
que la cualificación completa depende de CI para el commit exacto. No presentan
una selección focal como suite completa. Python, Swift, wheel/sdist y smoke de
instalación se califican íntegramente en CI. Los scripts de escritorio son
`script/test_desktop.sh` y `script/build_desktop.sh`; no recuperar instaladores
retirados. Un bundle de desarrollo con Python externo no es una distribución
autocontenida. No afirmar paridad por compilar o por usar un modelo de fixture.

## Garantías de diseño

- Una autoridad de autorización, ligada a operación, sesión, ejecución y argumentos.
- Cancelar entrega recibo; el siguiente run espera que termine la limpieza anterior.
- Texto de modelo y recibos de acciones son datos distintos. Un efecto incierto se
  conserva y no se repite automáticamente.
- Los adaptadores productivos necesitarán autenticación, firmas, límites, revocación
  y comprobación inmediatamente antes del efecto. El prototipo no acredita TCC.
- La investigación mantiene identidad, fuentes, hashes, Evidence IDs e incógnitas.
  Nunca presentar cobertura parcial o pruebas sintéticas como revisión exhaustiva.
- Los originales históricos se conservan en Git. Las copias de consulta normalizan
  sus nombres a Jarvis por orden del dueño; conservar capacidades, hashes y referencia
  al snapshot original, sin presentarlas como código activo ni como rutas originales.

Consultar `docs/rebuild/README.md`, `docs/rebuild/CLEAN_START.md` y
`docs/V1_PENDIENTES.md` antes de ampliar el alcance.
