# Context7 — primera integración MCP externa

Fecha: 2026-09-14. El propietario confirmó Context7 como primera integración de solo lectura.

## Resultado real

- Conexión HTTPS validada contra `https://mcp.context7.com/mcp` con el cliente de Jarvis.
- Catálogo real revisado: `resolve-library-id` y `query-docs`; huellas fijadas en código y pack.
- Ambas herramientas ejecutadas con una consulta pública fija sobre cancelación de tareas asyncio.
- Acceso anónimo funcionó; no se creó cuenta ni se solicitó/guardó una API key de Context7.
- Respuestas del smoke test: 1.793 bytes para resolución de biblioteca y 4.575 para documentación.
- Paquete instalado como `context7-docs` 1.0.0, habilitado y con sello local verificado intacto.
- El pack preexistente `research-first-pack` conservó su sello intacto.
- Simulación: Skill `context7-documentation`, exactamente dos herramientas de riesgo alto con
  confirmación obligatoria, destino `mcp.context7.com` y `execution=none`.

## Artefacto

Fuente portable: [context7-docs.jarvis-plugin.json](../../examples/plugins/context7-docs.jarvis-plugin.json).
Checksum canónico del paquete:
`9a52d1912eab02b01906f7f8f5ace0b2985069efffc3853c524ae1a481be35bc`.

Registro instalado: `~/Library/Application Support/Aegis/plugins/context7-docs.json`, modo `0600`.
El sello usa el mecanismo Keychain/HMAC existente de Jarvis; no se imprimieron claves.

## Controles

Solo capacidad `web_read`, nombres exactos, esquemas cerrados y huellas remotas revisadas. Antes
de cada llamada se comprueba el catálogo sin admitir herramientas adicionales. Cambios de contrato
bloquean la operación y requieren revisión; no se actualizan huellas automáticamente.

Cada consulta requiere aprobación de un solo uso. El filtro rechaza credenciales reconocibles,
emails, rutas personales, URLs, controles, bloques de código y entradas largas. No detecta toda
información confidencial; la revisión humana sigue siendo necesaria. El proveedor recibe consulta/IP.
El CLI `offline` no puede ser ampliado mediante starter tools de una Skill o MCP dinámico.

El transporte usa los metadatos obligatorios del protocolo stateless 2026-07-28. Rechaza respuestas
ambiguas, interacciones adicionales, `input_required`, errores no válidos, catálogos duplicados o
paginados y salida excesiva. `isError=true` se conserva como fallo, no como éxito de la herramienta.

## Verificación

- Suite completa: **1.780 aprobadas, 17 omitidas**, 33,56 segundos.
- Ruff de `src` y `tests`: correcto.
- `git diff --check`: correcto.
- Pruebas de esquema/pins, privacidad, confirmación y replay, errores HTTP/MCP, activación de Skill,
  compatibilidad de serialización de packs anteriores y bloqueo en modo ingeniería offline.
- Pruebas reales: `context7-catalogue` y `probe-context7`, fuera del sandbox por permisos de red.
- Verificación instalada y simulación: `plugins-verify` y `plugins-simulate` sin llamadas externas.

Las pruebas de red enviaron únicamente identificación técnica y preguntas públicas predefinidas.
No se envió código del proyecto, datos personales ni audio. Los tests completos son sintéticos;
las cualificaciones físicas/vocales siguen omitidas.

## App actualizada y carga verificada

Tras la autorización explícita del propietario, se reconstruyó y sustituyó
`~/Applications/Jarvis.app` con su daemon autocontenido. Verificación por IPC autenticado después
del reinicio: `context7-docs` 1.0.0, una Skill y exactamente dos herramientas; `research-first-pack`
sigue cargado. `security.status=intact`, audio `idle`, activación por voz deshabilitada.
El nuevo PID del daemon al verificar fue `83465`; el original anterior a la operación era `58060`.

- Compilación release arm64 con **Jarvis Local Development**, conservando el certificado y
  designated requirement originales. Firma estricta del bundle y **223 binarios Mach-O** verificados.
- Autoprueba del daemon congelado: **8/8**, sin red ni escrituras persistentes.
- Pruebas nativas repetidas: **266 aprobadas**. Regresión dirigida Python: **101 aprobadas**.
- Fuentes locales: base `eed2427ce047ce8402db6d91d375611a0b087f6c`, `AegisBuildDirty=true`.
  SHA-256 de entradas de compilación:
  `4e1d370c23f902534074f3451eaa14ba388fe442601fa41684585ece3605ac8f`.
- Copia anterior recuperable:
  `~/Applications/.jarvis-context7-update.jr9joC/previous.app`.
- El paquete aislado sin modelos personales permanece en
  `/private/tmp/aegis-distribution.YKDRLr/bundle/Jarvis.app` (temporal, no archivo de distribución).

La comprobación inicial interpretó la ausencia transitoria del socket durante el arranque como un
fallo y restauró automáticamente la app anterior. Se corrigió la espera para tolerar ese estado;
el segundo intento terminó con carga de plugins, salud y privacidad verificadas. No se borró ningún
bundle ni se reinició o reescribió el estado de auditoría para superar la comprobación.

Los dos modelos ya entrenados del bundle anterior se conservaron sin cambios **solo en la
instalación local** y se volvió a firmar su contenedor. No se entrenaron, capturaron ni reprodujeron
muestras. El modelo de activación sigue disponible (`wake_word=ready`). El arranque utilizó
overrides de `NSArgumentDomain`, no cambios de preferencias persistentes:

```text
-ai.aegis.voice.wake-word-enabled NO
-ai.aegis.proactive-alerts-enabled NO
```

Por tanto, escucha y alertas proactivas permanecen apagadas en esta sesión; otro arranque normal
volverá a respetar las preferencias guardadas. No se solicitó ninguna prueba física de voz.

El Mac tenía modo de bajo consumo: `runtime_state=suspended`, causa `low_power_mode`. Se preservó
esa política; cargar el plugin no fuerza al daemon a ejecutar trabajos mientras está suspendido.
Esta entrega comprueba la carga real en la app, no una nueva consulta end-to-end mediante el planner:
las dos consultas públicas remotas documentadas arriba se ejecutaron previamente con el cliente.

No se creó un segundo daemon de desarrollo, se cambiaron LaunchAgents, se tocaron permisos TCC,
se publicaron releases ni se modificaron los ZIP de distribución anteriores. Las guías macOS de
SwiftPM, ejecución, firma y empaquetado orientaron la compilación aislada, la identidad local y
la verificación antes y después de la sustitución.

Contrato y fuentes primarias: [ADR-0227](../architecture/ADR-0227-context7-read-only-connector.md).
