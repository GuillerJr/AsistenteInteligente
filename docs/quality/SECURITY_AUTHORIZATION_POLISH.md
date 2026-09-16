# Pulido 6 — Seguridad y autorizaciones

Base: `eed2427`. Fecha: 2026-09-14.

## Cambios y regresiones

| Frontera | Comprobación |
| --- | --- |
| Consentimiento exacto | Digest retenido, identidad de llamada, herramienta y JSON normalizado deben coincidir; cambiar tipos/valores falla, reordenar claves no. |
| Grant transaccional | Denegación o excepción del broker revoca el grant sobrante; una ejecución no puede aprovecharlo después. |
| Capacidad acotada | Grants recuperan espacio solo tras caducar; voz saturada no expulsa hashes activos y un invitado no consume reservas. |
| Relojes | Nonces y grants rechazan retroceso observado; la solicitud caduca a los 120 segundos monotónicos incluso con reloj de pared atrasado. |
| Voz estricta | Preguntas, citas, negación, elipsis, controles, probabilidades inválidas y booleanos/cadenas coercibles no autorizan. |
| Carreras de voz | Replay concurrente transcribe una vez; cancelar conserva el hash; cancelar/caducar el job durante verificación bloquea la aprobación tardía. |
| Integridad del runtime | Broker, ejecución aprobada antes/después de auditar, despacho síncrono/asíncrono y callbacks de recuperación/dispositivo bloquean entradas tras compromiso. |

Las pruebas iniciales reprodujeron 19 fallos de autorización/replay y dos fallos adicionales de
caducidad con reloj atrasado antes de corregir el código. Se añadieron 67 casos a la batería Python;
incluyen también pruebas positivas de operadores, reordenación de JSON y recuperación de capacidad.
La prueba de métricas usa ahora un reloj por fases, independiente del número de lecturas del reloj,
y conserva exactamente la exclusión del tiempo esperando consentimiento.

## Evidencia local

- Python completo: **1607 aprobadas, 17 opcionales omitidas**.
- Swift mediante `./script/test_native.sh`: **247 pruebas, 12 suites aprobadas**; compilación correcta
  del paquete AegisAudio (biblioteca, Jarvis y helpers).
- Ruff: sin incidencias.
- Contratos de aceptación: **25/25**; flujos deterministas: **20/20**.
- Fiabilidad prolongada: **400 ejecuciones, 1160/1160 checkpoints**, sin intentos de red ni tareas
  asíncronas filtradas; crecimiento RSS de 327680 bytes, dentro del presupuesto de 8 MiB.
- Las guías SwiftPM y Build/Run/Debug de macOS orientan la validación de compatibilidad mediante los
  scripts existentes; no se modifica código nativo, entitlements ni el botón Run.

```bash
.venv/bin/python -m pytest tests/test_confirmations.py tests/test_voice_confirmation.py \
  tests/test_ipc_protocol.py tests/test_job_authorization.py tests/test_job_tool_runtime.py \
  tests/test_security_authorization_boundary.py tests/test_jobs.py
.venv/bin/python -m pytest -o addopts='' -q
.venv/bin/ruff check src tests
./script/test_native.sh
./script/aegis.sh acceptance-benchmark
./script/aegis.sh production-workflows
./script/aegis.sh long-horizon-reliability
```

Los casos usan audio binario sintético, transcriptores/proveedores falsos, conectores sin red,
bases temporales y relojes controlados. No se capturó voz real, no se consultó el valor de la clave
NVIDIA ni se ejecutaron acciones externas para probar las denegaciones. Las pruebas no alteran
memoria, perfiles biométricos o preferencias reales.

## Alcance y límites

Sin ampliación de permisos, herramientas, scopes de red, persistencia biométrica o inferencias.
No se modifican el HUD, Menu Bar, cifrado existente ni selección de modelos. CLI NVIDIA intacto.
Las compuertas protegen entradas futuras, no revierten efectos ya iniciados. La caché vocal detecta
PCM idéntico por quince minutos dentro del mismo proceso: no es detección universal de replay,
autenticación acústica infalible ni prueba de presencia viva. Las marcas de reloj son por proceso;
esta revisión no sustituye una auditoría independiente ni una prueba acústica en vivo.
