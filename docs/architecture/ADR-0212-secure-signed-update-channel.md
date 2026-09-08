# ADR-0212: canal de actualización firmado y rollback atómico

- Estado: aceptado
- Fecha: 2026-09-08
- Responsable: Guillermo (`gzambrano27`)
- Bloque: P14

## Contexto

P13 convirtió `Jarvis.app` en un producto autocontenido, pero copiar una versión nueva encima de la
aplicación seguía dejando tres riesgos: aceptar un ZIP sustituido, instalar una versión antigua y
quedar sin una aplicación funcional si el primer arranque fallaba. La firma Developer ID protege el
código, pero por sí sola no expresa qué revisión pertenece al canal estable ni impide un downgrade
firmado con el mismo certificado.

## Decisión

P14 añade una segunda raíz de confianza, independiente de IPC y de Developer ID. La clave privada
Ed25519 vive exclusivamente en Keychain bajo `ai.aegis.update-signing/default`; el repositorio y cada
bundle contienen únicamente la clave pública fijada. El manifiesto `Jarvis.update.json` firma de
forma canónica la versión, número de build, revisión Git, ventana de validez, SHA-256 y tamaño del ZIP,
SHA-256 del manifiesto P12 y límites explícitos de privacidad.

Antes de modificar `~/Applications/Jarvis.app`, el instalador comprueba:

1. firma Ed25519, vigencia y clave pública fijada;
2. build estrictamente mayor al instalado;
3. correspondencia exacta entre manifiesto P12, ZIP e `Info.plist`;
4. ausencia de rutas peligrosas, archivos cifrados, duplicados, bombas ZIP y biometría personal;
5. firma Developer ID, Team ID, Hardened Runtime, ticket de notarización y Gatekeeper;
6. daemon actual íntegro, misma revisión e inactivo antes del intercambio;
7. confirmación explícita de la revisión de destino.

El intercambio usa renombres en el mismo volumen APFS. Un diario privado y durable registra
`prepared`, `swapped` y `committed`. La nueva app solo puede arrancar durante `swapped` si recibe el
UUID exacto del proceso instalador aún vivo. El instalador espera salud IPC e integridad; si el plazo
vence, restaura el bundle anterior, registra el rollback en la cadena de auditoría y vuelve a probar
la salud de la versión restaurada.

## Invariantes

1. Una actualización nunca se decide usando redirecciones, nombres de archivo o metadatos sin firma.
2. La clave privada del canal no entra al bundle, al ZIP, a Git ni a la salida del CLI.
3. Ningún build igual o menor puede instalarse, aunque su firma sea válida.
4. El bundle nuevo conserva bundle ID, Team ID y clave pública del canal.
5. Audio, voz, capturas, prompts, memorias, credenciales y modelos biométricos quedan fuera del ZIP.
6. El JSON no se deserializa como estado confiable hasta superar límites, esquema y firma.
7. Un primer arranque fallido no reemplaza definitivamente una versión sana.
8. P14 no aprueba sin evidencia P13 del mismo commit y el mismo artefacto.

## Consecuencias

El canal estable tiene una ventana máxima de 31 días y exige un número de build monotónico. La
rotación de la clave pública es deliberadamente un proceso fuera de banda: perder la clave privada
no autoriza a sustituir silenciosamente la raíz fijada. La aplicación local ad-hoc sirve para
desarrollo, pero una actualización comercial exige que tanto la versión instalada como la candidata
sean artefactos Developer ID notarizados del mismo equipo Apple.

P14 no descarga actualizaciones automáticamente. Esa omisión es intencional: la autenticidad, la
instalación confirmada y el rollback son el núcleo de seguridad; un servicio de catálogo o CDN puede
añadirse después sin entrar en la frontera de confianza.

Los builds Developer ID usan por defecto el tiempo Unix UTC como `CFBundleVersion`, que es monotónico
y permanece dentro del límite entero firmado hasta 2038. El publicador puede fijarlo con
`AEGIS_BUILD_NUMBER`; valores vacíos, cero, negativos o mayores que 2147483647 se rechazan.
