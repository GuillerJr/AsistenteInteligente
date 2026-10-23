# Primera conexión: Codex con ChatGPT (23-10-2026)

El dueño retiró la exclusividad NVIDIA y eligió esta conexión primero. La decisión
está en [ADR-0235](../architecture/ADR-0235-configured-inference-and-codex-chatgpt.md).

## Implementación

- El daemon y el CLI usan un único transporte de inferencia configurado: el cliente
  oficial Codex App Server. La sesión se inicia desde Ajustes y la completa el dueño.
- Estado y HOME propios, directorio vacío, perfil de solo lectura sin red para
  comandos y verificación de permisos efectivos. Ninguna herramienta nativa de
  Codex sustituye al broker de Jarvis; las propuestas pasan sus validaciones.
- Catálogo real de modelos, selección persistente y guardado explícito. Un modelo
  retirado deja visible el catálogo para poder repararlo, sin reemplazo silencioso.
- IPC de configuración saneada: solo estado y metadatos de modelos. El enlace de
  acceso se entrega con el recibo inicial; no se publican credenciales ni la cuenta.
- Cancelación coordinada, plazos inferiores a los del cliente nativo y bloqueo de
  cambios de modelo/sesión mientras hay trabajos activos.
- Los embeddings usan Apple con su identidad de índice. No se mezclan vectores NVIDIA.
  El daemon y el CLI productivos no inicializan clientes NIM ni consultan su clave.
- Los permisos persistentes de control de apps usan un almacén nuevo: el permiso
  anterior para enviar observaciones a NVIDIA no se reutiliza para Codex.
- Voz y notas de audio de Telegram quedan sin configurar. El chat funciona de forma
  independiente. Readiness 3.0 declara `voice_output`; las calificaciones de voz
  rechazan evidencia antigua si falta un proveedor de audio operativo.
- La evaluación y las métricas distinguen Codex de los registros NVIDIA heredados.

## Cobertura y comprobaciones

Pruebas del protocolo JSON-RPC, perfil aislado, credenciales ausentes, inicio y
cancelación de sesión, eventos de acceso fallido, catálogo, selección retirada,
visión, salida estructurada, herramientas no ofrecidas, respuestas incompletas,
interrupción y cierre. Pruebas del contrato IPC y SwiftUI, cancelación durante el
inicio de sesión y conservación del texto cuando falta salida de voz.

El arranque aislado con Codex CLI 0.163.0 confirmó el perfil efectivo y una sesión
efímera sin iniciar sesión ni enviar una consulta. Las pruebas del daemon usan
secretos sintéticos y prohíben inicializar NVIDIA; verifican chat/ingeniería,
procedencia Codex, audio sin configurar y una respuesta de configuración que supera
el plazo genérico de cuatro segundos.

Validación reproducible del candidato:

```bash
.venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/python -m pytest -p no:warnings
./script/test_native.sh
.venv/bin/python script/sync_localizations.py --check
./script/aegis.sh release-scope
```

Los hooks conservan la suite completa, empaquetado Release y los contratos de
aceptación, flujos productivos y fiabilidad. Esos contratos deterministas no
representan consultas reales a la cuenta del dueño.

## Comprobación que necesita al dueño

Tras instalar la app firmada: Ajustes › Modelos de IA › Iniciar sesión con ChatGPT ›
Abrir inicio de sesión. El dueño completa el acceso oficial. Después se ejecuta
«Probar conexión» y se verifica un chat temporal sintético desde la app.

P1-5/R2-3 siguen abiertos hasta completar TextEdit «Siempre», repetición en otro
chat y revocación. Las pruebas simuladas y el cambio de proveedor no demuestran
por sí solos que esas acciones reales funcionen. El audio, Ollama y otras APIs no
forman parte de esta primera conexión.
