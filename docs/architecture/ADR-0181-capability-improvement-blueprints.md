# ADR-0181: blueprints auditables de mejora de capacidades

## Estado

Aceptada.

## Contexto

La investigación acotada de una capacidad ausente conserva evidencia útil, pero no determina cómo
incorporarla sin ampliar autoridad. Entregar directamente esa decisión a un modelo mezclaría datos
web no confiables con diseño ejecutable y podría crear una vía de auto-modificación.

## Decisión

1. Jarvis deriva un blueprint puro y determinista de cada registro de aprendizaje; no persiste un
   segundo estado mutable.
2. El blueprint clasifica preparación, ruta de integración, riesgo, prioridad, dominios de revisión,
   restricciones de seguridad y pruebas de aceptación.
3. Las rutas permitidas son un adaptador nativo acotado, un atajo preexistente por nombre exacto o
   un Capability Pack declarativo. Ninguna permite código descargado, shell o permisos implícitos.
4. Una intención de escritura, envío, compra, instalación, publicación o eliminación se marca como
   crítica y exige revisión sensible. Toda mutación conserva confirmación de un solo uso.
5. El planner local puede usar el blueprint y la evidencia validada. NVIDIA no recibe esta memoria.
6. El CLI lista blueprints por prioridad y permite inspeccionarlos. La evidencia visible al operador
   se limita a título y URL; los extractos no salen del contexto local acotado.
7. Un blueprint nunca registra herramientas, instala paquetes, concede TCC ni evita el Tool Broker.
   La implementación posterior debe agregarse al núcleo o instalarse como artefacto firmado y pasar
   sus propias pruebas y revisión.

## Consecuencias

- Una brecha repetida se convierte en backlog técnico concreto y priorizado, no en una promesa vaga.
- La clasificación es conservadora y puede requerir revisión manual antes de elegir una integración.
- Aprender y diseñar siguen separados de autorizar y ejecutar; no existe autopromoción de código.
