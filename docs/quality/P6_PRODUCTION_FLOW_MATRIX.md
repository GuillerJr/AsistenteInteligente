# P6 — Matriz de flujos de producción

P6 no afirma que Jarvis pueda completar cualquier tarea de macOS. Su objetivo es más estricto:
demostrar, de forma repetible y sin efectos reales, que las fronteras críticas cooperan
correctamente antes de iniciar pruebas sobre aplicaciones reales.

El gate se ejecuta con:

```bash
./script/aegis.sh production-workflows
```

El manifest inmutable de esta matriz tiene SHA-256
`774e228a0c719909c049e0c9035f6950b67237903a86accf186dcec8b59125ca`. Cambiar un identificador,
categoría o checkpoint modifica esa huella y hace visible el cambio de cobertura.

## Contrato contra falsos positivos

Cada flujo declara sus checkpoints antes de ejecutarse. Para aprobar debe satisfacer todos una sola
vez, terminar en menos de cinco segundos y no realizar ningún intento AF_INET/AF_INET6. Una función
vacía, una verificación omitida, una excepción capturada que intente usar red o una espera infinita
no puede producir un resultado verde. El reporte contiene únicamente identificadores, contadores,
latencias y códigos acotados; nunca incluye entradas, respuestas, rutas, URLs o transcripciones.

## Los 20 flujos

| ID estable | Estados y frontera demostrados |
|---|---|
| `conversation.mode_transition` | Conversación → tarea; el follow-up solo permanece abierto en el modo permitido. |
| `conversation.support_quality` | Detecta soporte, acepta una respuesta empática y respeta el presupuesto de frases. |
| `conversation.repair_transition` | Entra en reparación, cierra el follow-up y limita la respuesta correctiva. |
| `conversation.identity_boundary` | Rechaza afirmaciones de identidad humana y dependencia emocional. |
| `orchestration.three_step_contract` | Ejecuta tres autorizaciones en orden, verifica cada resultado y no expone el objetivo. |
| `orchestration.modal_self_correction` | Detecta un bloqueo, corrige una vez y valida el reintento. |
| `orchestration.failure_halts_downstream` | Un primer paso fallido detiene el plan antes de tocar el siguiente. |
| `orchestration.denied_step_pruned` | Una autorización denegada no entra al contrato; la permitida sí se ejecuta. |
| `automation.browser_disambiguation` | Dos navegadores activos fuerzan selección; la elección conserva la consulta. |
| `automation.ax_then_pid_fallback` | AX es primario; PID es solo fallback y nunca interviene el cursor físico. |
| `automation.modal_recovery_bounds` | Recupera un modal y pide intervención tras tres ciclos si no es resoluble. |
| `automation.stale_context_rejected` | Una huella visual obsoleta impide cualquier evento AX o PID. |
| `memory.encrypted_roundtrip` | Recupera el recuerdo, verifica digest y demuestra ausencia de texto plano en SQLite. |
| `memory.namespace_isolation` | Bloquea lectura y búsqueda cruzadas entre namespaces. |
| `memory.session_reset_preserves_preferences` | Elimina sesión y memoria episódica, conservando preferencias durables. |
| `memory.fifo_sliding_window` | Expulsa el recuerdo más antiguo, conserva el nuevo y respeta capacidad. |
| `security.tool_boundaries` | Deniega herramienta y rol inválidos; exige confirmación en operación sensible. |
| `security.secret_and_voice_gates` | Bloquea secretos, voz bajo umbral y voz no enlazada al propietario. |
| `security.ipc_integrity_and_ceiling` | Reconstruye chunks; rechaza HMAC alterado y más de 256 KiB. |
| `security.runtime_failure_containment` | Cancela workers, aísla el proveedor y conserva el latch de compromiso. |

## Inventario honesto de capacidades

### Calificadas por el gate determinista

- Clasificación conversacional y evaluación local de calidad.
- Plan–Execute–Reflect con orden, poda de denegaciones y detención ante fallo.
- Automatización AX-first, fallback dirigido al PID, huella visual y recuperación acotada.
- Desambiguación de navegador antes de ejecutar.
- SQLite cifrado, namespaces, reset de sesión y FIFO.
- Broker de herramientas, biometría lógica, chunks HMAC y ciclo de vida de workers.

### Calificadas únicamente con evidencia del Mac real

Estas capacidades no se simulan como si fueran hardware. `jarvis_beta.sh daily` y
`macos_qualification_gate.sh` comprueban por separado bundle arm64, firma, Keychain, socket,
LaunchAgents, micrófono, Apple Speech, Screen Recording, Accesibilidad, wake word y perfil de voz.
Un estado térmico o Low Power Mode puede devolver `blocked` o `suspended` correctamente.

### No afirmadas por P6

- Éxito contra cada versión o disposición visual de Chrome, Safari, Mail, Finder o Xcode.
- Precisión biométrica real del propietario; requiere una muestra física de Guillermo.
- Disponibilidad, latencia o calidad de NVIDIA NIM o cualquier servicio externo.
- Notarización pública sin credenciales válidas de Apple Developer.
- Resistencia acumulativa de los contratos durante tareas largas; corresponde a P7. El éxito sobre
  cada versión y disposición de una aplicación real requiere evidencia específica del driver y no
  se deduce de un benchmark local.

## Línea base de cierre

La ejecución de cierre produjo `20/20`, cinco categorías completas, todos los checkpoints y cero
intentos de red. El benchmark contractual anterior permanece independiente en `25/25`; P6 no lo
reemplaza. El pre-push, el harness evolutivo, la beta diaria y la calificación manual de macOS
ejecutan ahora ambos gates.
