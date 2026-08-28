# ADR-0182: dossiers no ejecutables para implementar capacidades

## Estado

Aceptada.

## Contexto

Un blueprint identifica riesgo y ruta, pero todavía faltan decisiones concretas para construir una
integración. Generar directamente una Skill o un plugin válido obligaría a inventar nombres de
atajos, endpoints, permisos o esquemas y podría convertir evidencia web en autoridad instalable.

## Decisión

1. Jarvis deriva bajo demanda un dossier determinista del registro íntegro y su blueprint; no crea
   otro almacén ni ejecuta modelos.
2. El dossier propone solo uno de tres artefactos: diseño de herramienta del núcleo, contrato de
   Shortcut o borrador conceptual de Capability Pack.
3. Cada ruta enumera sus decisiones obligatorias, secuencia mínima, controles de seguridad y pruebas
   de aceptación. La evidencia expuesta contiene únicamente título y URL.
4. La compuerta es `research_required`, `owner_review` o `security_review`. Una operación crítica
   investigada nunca puede omitir la revisión de seguridad.
5. `execution_allowed` usa el tipo literal `false`. El dossier tiene esquema propio, no coincide con
   SkillDraft ni PluginManifest y no puede instalarse con los comandos existentes.
6. Un SHA-256 canónico detecta cambios accidentales. No se interpreta como firma ni reemplaza la
   firma, checksum y revisión del futuro artefacto ejecutable.
7. `capabilities-plan <gap_id>` imprime el dossier para revisión local; no escribe código, no concede
   permisos y no modifica el registro de aprendizaje.

## Consecuencias

- Jarvis convierte investigación en una especificación accionable sin inventar integración.
- El propietario puede ver exactamente qué falta antes de autorizar desarrollo o conexión externa.
- La promoción a capacidad real continúa siendo un proceso separado, explícito, probado y firmado.
