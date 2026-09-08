# P12 — Distribución firmada, notarizada y aceptada por Gatekeeper

P12 responde una pregunta concreta: ¿el mismo código aprobado para el piloto local puede entregarse
a otro Mac como un ZIP auténtico, sin datos biométricos privados y aceptado por las defensas nativas
de macOS?

## Compuerta

La ejecución final requiere que la identidad y el perfil de notarización ya existan en Keychain:

```bash
AEGIS_CODESIGN_IDENTITY="Developer ID Application: Nombre (TEAMID)" \
AEGIS_NOTARY_PROFILE="jarvis-notary" \
./script/p12_distribution_release_gate.sh
```

La compuerta no recibe contraseñas, claves privadas ni credenciales por argumentos. El nombre del
perfil solo permite que `notarytool` consulte el registro seguro creado previamente en Keychain.

La secuencia es estricta:

1. exige un árbol tracked limpio y las dos variables de identidad;
2. vuelve a ejecutar P11 completo, incluida la evidencia vocal física P10;
3. compila arm64, firma con timestamp y Hardened Runtime, y genera el ZIP sin modelos personales;
4. envía el ZIP a Apple, espera un veredicto `Accepted`, aplica el ticket y regenera la evidencia;
5. extrae el ZIP final en `/private/tmp` con modo privado y vuelve a ejecutar `codesign`, `stapler`
   y `spctl` sobre ese artefacto exacto;
6. publica atómicamente `dist/Jarvis.distribution.json` solo si los cinco contratos alcanzan 100.

## Los cinco contratos

| Contrato | Condición de aprobación |
|---|---|
| `release.pilot_chain` | P11 obtuvo `5/5`, pertenece al commit actual y conserva todas sus garantías. |
| `release.provenance` | Árbol fuente, ZIP, miembros, manifiesto y SBOM coinciden byte por byte. |
| `security.developer_id` | Developer ID, Team ID, timestamp, Hardened Runtime y entitlements seguros. |
| `security.apple_notarization` | Ticket stapled válido y Gatekeeper acepta el bundle del ZIP final. |
| `privacy.distribution_boundary` | No hay modelos personales ni contenido del usuario en el paquete o evidencia. |

## Seguridad del análisis

El lector solo acepta archivos regulares propiedad del usuario, modo `0600` y tamaños acotados.
Antes de extraer valida todas las rutas y metadatos ZIP. La extracción es efímera; se elimina al
terminar incluso si una prueba falla. El reporte solo guarda hashes, booleanos y contadores.

## Estado y límite

El código y las pruebas de P12 pueden quedar completos aunque la ejecución física permanezca
`qualification_pending`. Un resultado comercial válido no puede generarse hasta cerrar P10/P11 y
obtener un veredicto real de Apple. P12 certifica distribución Developer ID directa; no afirma que
Jarvis cumpla las reglas o el modelo de sandbox de la Mac App Store.
