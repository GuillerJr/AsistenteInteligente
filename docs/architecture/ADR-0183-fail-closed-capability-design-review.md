# ADR-0183: revisión fail-closed de diseños de capacidad

## Estado

Aceptada.

## Contexto

El dossier enumera lo que falta, pero marcarlo como revisado sin validar cobertura permitiría omitir
permisos, pruebas o controles. Tratar la revisión del diseño como autorización de ejecución también
rompería la separación entre aprendizaje, desarrollo y uso de herramientas.

## Decisión

1. `capabilities-review` consume un archivo JSON local regular, sin symlinks y de hasta 16 KiB.
2. La declaración se vincula al `gap_id`, hash del dossier y compuerta vigentes. Cualquier cambio del
   dossier invalida la revisión previa.
3. Aceptar exige exactamente todas las entradas requeridas, todos los controles de seguridad, todas
   las pruebas de aceptación y el SHA-256 de su reporte; no admite campos extra ni índices duplicados.
4. Endpoints externos deben tener forma HTTPS pública, los modos de autenticación son `none` o
   `bearer`, las capacidades pertenecen al límite del runtime y los esquemas JSON pasan el mismo
   validador cerrado de Capability Packs. Ninguna credencial puede aparecer en la declaración.
5. Una compuerta crítica exige referencia explícita de revisión de seguridad. Una compuerta sin
   investigación no puede aceptar un diseño. Rechazar no puede afirmar entradas o pruebas validadas.
6. El veredicto se sella con SHA-256, expone solo las claves revisadas y mantiene
   `execution_allowed=false`. Su máximo estado es `ready_for_manual_implementation`.
7. El veredicto no es una firma, no se persiste, no registra herramientas y no es entrada válida para
   instalar Skills o Capability Packs.

## Consecuencias

- Una propuesta incompleta, obsoleta o sensible falla antes de llegar al desarrollo.
- El operador obtiene una comprobación reproducible sin convertir el CLI en un instalador.
- Implementar, firmar, instalar y ejecutar continúan siendo etapas independientes y auditables.
