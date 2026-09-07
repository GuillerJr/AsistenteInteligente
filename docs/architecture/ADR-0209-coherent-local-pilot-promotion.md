# ADR-0209: promoción coherente a piloto local y cierre de P11

- Estado: aceptado
- Fecha: 2026-09-07
- Responsable: Guillermo (`gzambrano27`)
- Bloque: P11

## Contexto

P6–P10 producen garantías diferentes: contratos, flujos, resistencia, driver nativo, navegador y
voz física. Cada reporte podía aprobar correctamente y aun así quedar asociado a otra revisión del
checkout, otra instalación o un ZIP regenerado después. La suma informal de resultados no es una
decisión reproducible de promoción.

## Decisión

`PilotReleaseQualificationGate` se convierte en la única frontera para declarar una revisión lista
para piloto local. Consume reportes P10 y macOS privados, el manifiesto de release, la SBOM, el ZIP,
el bundle instalado y el árbol Git. Verifica esquemas completos, consistencia de puntajes, checks
exactos, permisos `0600`, propiedad, tamaños acotados, hashes y revisión común.

`p11_pilot_release_gate.sh` ejecuta los prerrequisitos en orden. P10 conserva autoridad sobre la voz
real y falla antes de construir el candidato si la interacción está ausente. P11 genera únicamente
el perfil `local-development`; la promoción pública sigue separada porque depende de Developer ID y
del servicio de notarización de Apple.

## Invariantes

1. P11 nunca aprueba si P10 o la calificación macOS no alcanzan 100.
2. Toda evidencia pertenece al mismo commit completo de 40 caracteres.
3. El hash del árbol tracked del manifiesto coincide con el checkout limpio.
4. ZIP y SBOM coinciden byte por byte con las huellas del manifiesto.
5. El candidato no contiene los modelos de wake word o hablante del propietario.
6. El reporte final no contiene datos del usuario ni rutas locales.
7. Un archivo inseguro, mutado o ambiguo bloquea toda la promoción.

## Consecuencias

P11 cierra la entrega técnica para un piloto local verificable, no una release comercial. La marca
verde requiere completar primero la prueba física de voz del build instalado. Esa dependencia no
es deuda ni un error automatizable: es la evidencia humana que el contrato pretende demostrar.
