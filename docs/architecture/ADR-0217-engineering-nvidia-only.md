# ADR-0217 — CLI de ingeniería con inferencia NVIDIA exclusivamente

Fecha: 2026-09-10. Decisión autorizada por el dueño: usar APIs NVIDIA, no un modelo local.

## Motivo

La evaluación Apple no certificó la calidad semántica del CLI. El Mac dispone de 16 GiB compartidos;
agregar otro modelo residente necesita un presupuesto que aún no está aprobado. No se descargan
pesos ni se contrata un servicio de pago. La API existente se autentica con la clave del Llavero.

## Contrato

- `jarvis` y `engineering.submit` nuevos usan `nvidia_only`. `local_only`/`hybrid` se conservan
  únicamente como políticas optativas; los clientes antiguos con metadatos explícitos conservan
  su comportamiento. El resto del asistente no cambia de política.
- La ruta remota salta AFM, MLX, confianza y especulación. Nunca usa fallback local, ni ante cuota,
  timeout, error de autenticación o fallo de síntesis después de leer un archivo.
- El historial de la sesión, la petición y las referencias son mensajes separados. Solo pueden
  salir referencias `workspace`, `repository_inventory` y `tool_results`, con redacción de patrones
  sensibles. No se consulta ni transmite el GraphRAG personal. No hay audio, cámara ni biometría.
- Cambiar de política en el CLI reinicia el contexto, no borra memoria persistente. Los filtros son
  defensa heurística; código confidencial requiere autorización del responsable antes de enviarlo.
- El broker conserva confinamiento al subworkspace, validación de argumentos, límites de lectura y
  confirmaciones. NVIDIA propone; el Mac decide y ejecuta. El perfil sigue siendo de lectura, no
  edición arbitraria, shell ni publicación Git.
- Con inventario completo vacío no se ofrece una lectura inexistente; esto permite conversación
  en streaming sin esperar una llamada de herramienta que no tiene trabajo que hacer.
- Un 429 activa inmediatamente la pausa global antes de intentar un segundo modelo. 401/403 se
  distinguen de cuota y de red. La carga del secreto para chat no bloquea el event loop IPC.
- Auditoría solo de modelo, rol, duración y resultado; no de prompts, fuentes o credenciales.

## Verificación

La suite determinista comprueba la ausencia de invocaciones locales, historial, lectura real
mediante broker, exclusión de memoria privada, redacción, reset de política y cooldown compartido.
`AEGIS_RUN_NVIDIA_ENGINEERING_PROBE=1 .venv/bin/pytest -q -s tests/test_engineering_nvidia_live.py`
ejecuta únicamente ejemplos sintéticos optativos contra el proveedor real. El pre-commit y
pre-push no consumen API.

La primera evaluación Pro confirmó diálogo y lectura de código, pero una petición expiró a los
45 segundos. Una API remota no garantiza latencia instantánea ni calidad universal. No se
incrementa el timeout indefinidamente ni se interpreta una respuesta como código ejecutado.

La selección final usa Nemotron para conversación sin lecturas y síntesis; DeepSeek Pro/Flash
cuando se ofrecen herramientas de repositorio o revisión crítica. Nemotron entregó un primer
fragmento a los 0,39 s en un turno caliente, pero otro escenario alcanzó el timeout. No es un SLA:
la latencia de los endpoints varía y estos resultados no certifican perfección del modelo.

## Disponibilidad externa

Verificados en NVIDIA Build: [Nemotron Lightning](https://build.nvidia.com/nvidia/nemotron-3.5-lightning-30b-a3b),
[DeepSeek Pro](https://build.nvidia.com/deepseek-ai/deepseek-v4-pro-0813) y
[DeepSeek Flash](https://build.nvidia.com/deepseek-ai/deepseek-v4-flash-0731).
Los endpoints gratuitos son de prueba y están sujetos a cuota, cambios y retirada. No se usa como
nuevo valor por defecto GPT-OSS-120B ni Qwen3-Coder-480B: el catálogo ya marcaba sus endpoints
gratuitos como retirados al verificar esta decisión.
