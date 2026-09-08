# ADR-0210: promoción notarizada y cierre de implementación de P12

- Estado: aceptado
- Fecha: 2026-09-08
- Responsable: Guillermo (`gzambrano27`)
- Bloque: P12

## Contexto

P11 produce un piloto local coherente, pero no demuestra que un tercero pueda abrir Jarvis sin una
advertencia de Gatekeeper. El perfil local usa firma ad hoc y no representa una distribución
comercial. La existencia de un ZIP o incluso una respuesta aceptada de `notarytool` tampoco basta:
el artefacto puede cambiar después, perder su ticket o contener entitlements de depuración.

## Decisión

`DistributionReleaseQualificationGate` es la única frontera autorizada para declarar una revisión
lista para distribución fuera del Mac del propietario. La compuerta P12 vuelve a ejecutar P11,
construye el paquete sin modelos biométricos personales, exige Developer ID, lo envía al servicio de
notarización de Apple y vuelve a empaquetarlo después del stapling. Después extrae exactamente ese
ZIP en un directorio privado y efímero para verificarlo con `codesign`, `stapler` y `spctl`.

El validador autentica el inventario completo del ZIP contra el manifiesto, la SBOM SPDX 2.3, la
revisión Git y el hash del árbol tracked. Rechaza rutas de escape, enlaces simbólicos, duplicados,
miembros cifrados, expansión fuera de presupuesto y modelos de wake word o identidad del dueño.

Los entitlements necesarios deben estar presentes. También se rechazan explícitamente
`get-task-allow`, memoria ejecutable sin firma, desactivación de Library Validation y otras
excepciones que debiliten Hardened Runtime.

## Invariantes

1. P12 nunca aprueba sin un P11 `5/5` perteneciente a la misma revisión.
2. La firma debe ser Developer ID Application, incluir Team ID, timestamp seguro y Hardened Runtime.
3. El ticket stapled y la evaluación de Gatekeeper se validan sobre el bundle extraído del ZIP final.
4. Manifiesto, SBOM, archivo y cada miembro regular coinciden mediante SHA-256.
5. El artefacto contiene cero modelos biométricos del propietario y la evidencia no contiene datos
   de voz, imágenes, prompts, credenciales ni rutas absolutas.
6. NVIDIA y otros proveedores de modelos no intervienen. La única comunicación externa del flujo es
   el envío del paquete sin datos del usuario al servicio Apple Notary.
7. Una credencial ausente, una comprobación ambigua o un comando nativo fallido bloquea la promoción.

## Consecuencias

La implementación de P12 puede verificarse enteramente con fixtures locales. Su certificación real
requiere primero la interacción P10/P11, una identidad Developer ID válida y un perfil `notarytool`
guardado en Keychain. Esas dependencias externas no se sustituyen por mocks en el reporte final.
