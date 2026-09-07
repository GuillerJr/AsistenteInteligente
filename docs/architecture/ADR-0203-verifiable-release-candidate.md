# ADR-0203: Release candidata verificable y cierre de P5

- Estado: aceptado
- Fecha: 2026-09-07
- Prioridad: P5 — distribución operable y entrega
- Responsable físico: Guillermo (`gzambrano27`)

## Contexto

P4 vinculó checkout, app y readiness, pero `dist/Jarvis.zip` seguía siendo un archivo opaco. No
existía una SBOM, una huella del árbol fuente ni una lista autenticable de los miembros del bundle.
Además, el instalador guardaba temporalmente la app anterior, pero la eliminaba incluso cuando el
nuevo proceso no lograba arrancar. Una compilación exitosa no equivale a una entrega recuperable.

## Decisión

1. `release_macos.sh candidate` produce un candidato local sin fingir Developer ID o notarización.
2. Cada candidato genera `Jarvis.release.json` y `Jarvis.spdx.json` con modo privado `0600`.
3. El manifest enlaza revisión Git completa, SHA-256 del árbol versionado, identidad/versiones del
   bundle, ZIP, SBOM y cada archivo regular contenido en la app.
4. El lector rechaza ZIP-slip, links simbólicos, duplicados, expansión mayor a 2 GiB, miembros
   mayores a 512 MiB, más de 4.096 entradas, árbol tracked sucio o revisión incoherente.
5. La SBOM SPDX 2.3 se deriva exclusivamente de `uv.lock` y `Package.resolved`; una licencia no
   conocida se declara `NOASSERTION` y nunca se inventa.
6. Los perfiles `developer-id` y `developer-id-notarized` reutilizan la misma verificación. El
   segundo regenera evidencia después del stapling, porque el ticket cambia los bytes del artefacto.
7. El instalador conserva la app anterior dentro de staging hasta probar firma, LaunchAgent y
   arranque. Un fallo restaura la versión previa; si su firma tampoco es válida, la conserva sin
   ejecutarla.
8. La edición completa se declara Desktop Developer ID. No se afirma compatibilidad con App Store.
9. Los modelos locales `JarvisWakeWord.mlmodelc` y `JarvisSpeakerIdentity.mlmodelc` pertenecen al
   perfil privado del propietario. La instalación local puede incorporarlos, pero desactiva la
   emisión del ZIP; cualquier artefacto de distribución que los contenga se rechaza.

## Invariantes de aceptación

1. Cambiar un byte del ZIP, manifest o SBOM invalida la verificación.
2. Ningún documento contiene prompts, transcripciones, imágenes, credenciales o paths absolutos.
3. El manifest no puede emitirse desde una revisión tracked sucia.
4. El ZIP siempre pertenece al mismo commit declarado por `AegisBuildRevision`.
5. La ausencia de credenciales Apple bloquea notarización sin bloquear un candidato local honesto.
6. Un fallo posterior al intercambio del bundle activa rollback antes de salir.
7. Una release candidata no contiene modelos de wake word o identidad vocal del propietario.

## Consecuencias

P5 queda cerrado como frontera de entrega técnica reproducible y recuperable. El proyecto puede
generar y verificar localmente una release candidata completa. La publicación comercial continúa
requiriendo dos pruebas externas que el código no puede fabricar: una identidad Developer ID del
propietario y un veredicto aceptado de Apple Notary Service.
