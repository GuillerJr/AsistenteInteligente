# Modelo de amenazas de Jarvis Desktop

- Versión del modelo: 1.0
- Responsable: Guillermo (`gzambrano27`)
- Perfil de distribución: Desktop mediante Developer ID; no App Store
- Principio: local-first, mínimo privilegio y fallo cerrado

## Activos protegidos

Jarvis protege la clave HMAC de IPC, secretos de proveedor, memoria cifrada, identidad vocal,
historial conversacional, audit log, contenido efímero de pantalla/cámara/audio y autoridad para
actuar sobre aplicaciones. El cursor físico, la configuración de macOS y los datos de otras cuentas
no forman parte de la autoridad implícita del agente.

## Fronteras de confianza

1. La app Swift posee sensores, TCC, biometría y presentación de aprobaciones.
2. El daemon Python orquesta modelos, memoria y políticas sin privilegios de root.
3. `aegis.sock` acepta únicamente mensajes acotados y autenticados por HMAC desde el usuario local.
4. Keychain conserva claves; SQLite y `audit.jsonl` se tratan como almacenamiento manipulable.
5. AX, ScreenCaptureKit, Vision, SoundAnalysis y Apple Events permanecen sujetos a TCC.
6. NVIDIA y la web son fronteras remotas opcionales; reciben contexto reducido y redactado.
7. MCP, plugins, páginas, correo, documentos y salida de herramientas son entrada no confiable.

## Amenazas y controles

| Amenaza | Control principal | Estado residual |
|---|---|---|
| Proceso local falsifica IPC o desborda RAM | HMAC por frame, secuencia estricta, 16 KiB por chunk y 256 KiB por mensaje | Un proceso del usuario con acceso al Keychain conserva su autoridad de cuenta |
| Prompt injection indirecta | Resultados marcados como datos no confiables, poda de tools y broker por schema/política | El contenido puede degradar calidad; no debe ampliar capacidades |
| Confusión de ventana/PID | Bundle, PID, fecha de lanzamiento y huella de observación ligados a cada acción | Cambios legítimos de UI pueden exigir intervención |
| Replay de aprobación vocal | Identidad del propietario, semántica permitida, TTL/hash y alternativa Touch ID | Audio generado novedoso exige defensa adicional contra deepfakes |
| Robo o edición de memoria | AES-GCM con AAD por namespace, blind indices, hash de contenido y permisos privados | Una sesión ya desbloqueada hereda la seguridad de la cuenta macOS |
| Rollback o truncado de auditoría | Hash chain, ancla Keychain y bloqueo ante divergencia | Compromiso simultáneo de cuenta y Keychain queda fuera del adversario local limitado |
| Captura visual o acústica persistente | Búferes en memoria, límites estrictos y cero archivos transitorios de visión | Crash dumps del sistema operativo dependen de la política del host |
| SSRF/exfiltración | Allowlist HTTPS pública, bloqueo de destinos privados y redacción previa al proveedor | Una fuente pública autorizada sigue pudiendo observar la IP pública |
| Helper o ZIP sustituido | Codesign, Hardened Runtime, revisión exacta, SHA-256, SBOM y manifest de provenance | La distribución pública requiere notarización real de Apple |
| Modelo biométrico filtrado en un ZIP | El build de distribución excluye modelos personales y el inspector rechaza sus rutas | El propietario conserva esos modelos únicamente en su instalación privada |
| Instalación defectuosa | Staging, verificación antes del reemplazo y rollback automático si no arranca | Un fallo simultáneo del bundle anterior se conserva, pero no se ejecuta sin firma válida |
| Operaciones sobreviven al cierre y modifican la auditoría después del sello | Admisión cerrada, drenaje de clientes/jobs/workers y unión de hilos antes del ancla; supervisor conserva el PID hasta terminar | Un proceso bloqueado puede requerir SIGKILL; el siguiente arranque no acepta un sello incoherente |
| Respuesta o archivo inyecta ANSI/OSC52/bidi en el CLI | Escape de controles antes de stdout/stderr; solo estilos propios generan ANSI | El contenido aún puede engañar semánticamente; nunca concede permisos |
| Desconexión del CLI duplica una operación o declara una cancelación ficticia | Sin reenvío automático; tarea ligada a job/request/conversación y versión; `/resume`; cancelación autenticada | Si se pierde la respuesta al envío inicial, su recepción es incierta y exige revisar el HUD |
| Historial de terminal expone instrucciones o secretos | Editor sin persistencia, 100 entradas/64 KiB, filtro de credenciales y limpieza con `/new` | El scrollback del terminal y las conversaciones cifradas tienen ciclos de vida independientes |

## Decisiones de distribución

La edición completa necesita Accessibility, Apple Events, Screen Recording, micrófono y cámara bajo
consentimiento TCC. Estas capacidades no son coherentes con afirmar compatibilidad total con App
Sandbox. La distribución soportada es Developer ID + Hardened Runtime + notarización. Una edición
App Store futura debe ser otro target reducido, sin computer-use general ni automatización arbitraria.

## Exclusiones

No se promete resistencia frente a root, kernel comprometido, firmware malicioso, propietario que
entrega sus credenciales o manipulación física del equipo desbloqueado. Jarvis tampoco convierte
la inferencia probabilística en autorización: las acciones críticas continúan sujetas al broker.

## Evidencia exigida por release

Cada candidato debe vincular commit, árbol fuente, bundle, archivos internos, ZIP y SPDX mediante
`Jarvis.release.json`; `release_evidence.py verify` debe detectar cualquier modificación. Las
pruebas deterministas no sustituyen TCC real, biometría calibrada, Developer ID ni notarización.
