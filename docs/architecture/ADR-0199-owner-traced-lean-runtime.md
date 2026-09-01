# ADR-0199: Runtime espartano con requisitos trazables al dueño

- Estado: aceptado
- Fecha: 2026-09-01
- Responsable físico: Guillermo (`gzambrano27`)

## Requisitos cuestionados

| Regla | Riesgo real | Decisión |
|---|---|---|
| Rotar `audit.jsonl` mediante `newsyslog` | Una rotación externa cambia el inode y separa el archivo de la punta SHA-256 guardada en Keychain. El daemon activo lo interpreta correctamente como sustitución y revoca la confianza. | Se conserva el límite local de 16 MiB y la rotación externa queda prohibida hasta que exista un protocolo de cierre, anclaje y apertura de segmentos autenticados. |
| Eliminar la caché SHA-256 anti-replay de voz | La autorización por voz es una alternativa a Touch ID, no un factor que siempre lo acompaña. Sin unicidad temporal, una grabación válida puede reutilizarse. La caché está acotada a 512 hashes y consume decenas de KiB, no un presupuesto térmico o de memoria material. | Se conserva la defensa anti-replay. Quitarla requeriría cambiar el contrato de producto a voz **y** Touch ID para cada acción. |
| Recuperar Chrome mediante JXA | Dos motores DOM producen estados y permisos distintos; JXA además activa Chrome y roba foco. | Se elimina JXA. CDP loopback en `127.0.0.1:9222` es requisito explícito y cualquier indisponibilidad falla cerrada. |
| Similitud coseno en Python sin `sqlite-vec` | Duplica el índice, aumenta CPU y memoria unificada y oculta una instalación incompleta. | No existe ya esa ruta. El diagnóstico informa `memory_vector_acceleration=unavailable`; el esquema semántico falla cerrado y FTS5 permanece como recuperación léxica. |

## Simplificación y aceleración

La selección dinámica de herramientas MCP usa una consulta directa por el bundle identifier de la
aplicación frontal entregado por `NSWorkspace`. No tokeniza la orden ni calcula intersecciones. La
autorización de voz inicia ventanas semánticas locales a los 250 ms: SoundAnalysis recibe cada frame
de 32 ms y Whisper MLX verifica snapshots autenticados mientras Core Audio continúa capturando.

## Automatización

El hook versionado `.githooks/pre-commit` ejecuta el build Swift firmado localmente, el benchmark de
contratos y la calificación macOS viva. Cualquier fallo, una puntuación diferente de 100 o evidencia
operativa incompleta rechaza el commit. `script/setup_git_gates.sh` instala el hook sin copiar código
dentro de `.git`.
