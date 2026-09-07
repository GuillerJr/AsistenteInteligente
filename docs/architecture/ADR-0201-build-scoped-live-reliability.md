# ADR-0201: Evidencia operativa aislada por compilación y cierre de P3

- Estado: aceptado
- Fecha: 2026-09-07
- Prioridad: P3 — fiabilidad observable y validación real
- Responsable físico: Guillermo (`gzambrano27`)

## Contexto

La calificación macOS combinaba métricas persistentes de trabajos y evidencia
nativa de voz, visión y control acumuladas durante toda la vida de la instalación.
Ese diseño era incorrecto para certificar una versión: un fallo antiguo podía
penalizar indefinidamente un binario corregido y, en sentido contrario, una acción
correcta de una versión anterior podía hacer parecer validada una regresión nueva.

El resultado no distinguía con precisión entre cuatro causas operativas diferentes:
binario nativo desactualizado, aplicación Menu Bar detenida, Low Power Mode activo y
ausencia de interacciones reales del propietario. Sin esa separación, el reporte no
era una guía confiable para corregir el equipo.

## Decisión

La revisión Git completa de 40 caracteres es la frontera causal de una calificación:

1. el daemon publica su `build_revision` autenticado;
2. `jobs.metrics` incluye únicamente evaluaciones registradas por esa revisión;
3. la app nativa lee `AegisBuildRevision` de su `Info.plist` y lo persiste en sus
   snapshots privados;
4. al cambiar de revisión, los contadores operativos nativos se reinician sin borrar
   ni reinterpretar contenido del usuario;
5. la calificación rechaza identidades incoherentes y trata evidencia de otra
   compilación como una muestra vacía;
6. la migración de evaluaciones previas las marca como `legacy`: permanecen para
   diagnóstico, pero no participan en el resultado actual.

Los eventos nativos se registran con `OSLog` bajo la categoría `Reliability`. Sólo
incluyen revisión abreviada, contadores, booleanos y latencias; nunca prompts,
transcripciones, audio, imágenes, URLs o argumentos de herramientas.

## Razones operativas estables

La compuerta expone causas accionables y acotadas, entre ellas:

- `native_build_mismatch`: el daemon y la app no pertenecen a la misma revisión;
- `menu_bar_not_running`: la app nativa no está ejecutándose;
- `wake_word_disabled`: falta activar el detector configurado por el propietario;
- `real_owner_voice_sample_required`: se necesita una interacción vocal real de la
  revisión actual;
- `live_capture_and_verified_action_required`: faltan captura y acción verificadas;
- `current_build_samples_required`: todavía no existen veinte trabajos terminales;
- `low_power_mode_active` o `thermal_pressure_active`: el equipo no permite una
  medición sostenida válida.

La compuerta nunca cambia preferencias energéticas ni fabrica interacciones para
alcanzar una puntuación. Esas condiciones describen el estado real del Mac y deben
resolverse conscientemente por el operador.

## Invariantes de aceptación

1. Las métricas de una revisión no aparecen en `jobs.metrics` de otra revisión.
2. La migración v1 conserva las filas como `legacy` y no contamina la revisión activa.
3. La evidencia nativa se reinicia al detectar una revisión diferente.
4. Un snapshot nativo con identidad inválida no se persiste.
5. La calificación exige coincidencia exacta entre daemon, trabajos y aplicación.
6. Los reportes y logs de fiabilidad no contienen datos del usuario.
7. Low Power Mode, TCC, voz real y acciones reales permanecen como puertas honestas,
   no como casos simulados.

## Consecuencias

P3 queda cerrado en código: el producto tiene una medición reproducible, privada y
atribuible al binario que se está evaluando. La puntuación de hardware puede seguir
en estado `blocked` o `needs_interaction` hasta que el Mac esté en condiciones de
medición y el propietario complete las interacciones requeridas; eso es evidencia de
funcionamiento del diseño fail-closed, no deuda de implementación de P3.
