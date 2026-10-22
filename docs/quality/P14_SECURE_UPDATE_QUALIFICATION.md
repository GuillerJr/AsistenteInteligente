# P14 — Canal de actualización seguro y recuperación automática

P14 certifica que una distribución P13 puede evolucionar sin confiar ciegamente en un ZIP, sin
aceptar downgrades y sin dejar al usuario sin una versión operativa cuando el nuevo arranque falla.

## Preparación única del publicador

La raíz Ed25519 se crea una sola vez en el Mac autorizado para publicar:

```bash
.venv/bin/python script/update_channel.py init
```

El secreto queda en Keychain (`ai.aegis.update-signing`, cuenta `default`). El archivo versionado
`packaging/JarvisUpdatePublicKey.ed25519` contiene solo la clave pública. Si el secreto desaparece y
la clave pública ya existe, la herramienta falla cerrada; nunca rota la identidad por sorpresa.

## Compuerta comercial

```bash
AEGIS_CODESIGN_IDENTITY="Developer ID Application: Nombre (TEAMID)" \
AEGIS_NOTARY_PROFILE="jarvis-notary" \
./script/p14_secure_update_gate.sh
```

El flujo vuelve a ejecutar P13, produce un artefacto Developer ID notarizado, firma
`dist/Jarvis.update.json` y publica `dist/Jarvis.update-qualification.json` únicamente con puntuación
100. Todos los archivos de evidencia se mantienen privados (`0600`). El número de build se genera
de forma monotónica; un pipeline externo puede proporcionarlo mediante `AEGIS_BUILD_NUMBER`.

## Los cinco contratos

| Contrato | Condición de aprobación |
|---|---|
| `release.p13_chain` | P13 obtuvo 100 y autentica el mismo commit y ZIP. |
| `channel.ed25519_authenticity` | Firma, clave fijada, vigencia, manifiesto P12 y artefacto coinciden. |
| `channel.rollback_resistance` | El canal rechaza el mismo build y cualquier downgrade. |
| `installer.atomic_recovery` | El swap APFS y el diario durable restauran la versión anterior. |
| `privacy.update_boundary` | La evidencia no contiene datos personales ni provoca llamadas de red/modelo. |

## Verificación e instalación por el propietario

En la app, **Configuración → General → Acerca de y actualizaciones** permite buscar una
versión y confirmar **Instalar y reiniciar**. La comprobación descarga solo el manifiesto,
verifica Ed25519, vigencia y build. Después de confirmar, descarga y autentica el ZIP y la
evidencia de publicación; la instalación reutiliza el swap, la comprobación de salud y la
recuperación de `secure_update.py`. Una revisión distinta a la que se confirmó se rechaza.

El canal estable tiene una dirección fija:
`https://github.com/GuillerJr/AsistenteInteligente/releases/latest/download/`.
La publicación estable debe incluir `Jarvis.update.json`, `Jarvis.release.json` y `Jarvis.zip`
producidos por la compuerta notarizada; se recomienda adjuntar también `Jarvis.spdx.json`.
La consulta no usa tokens de GitHub ni credenciales del dueño, por lo que los artefactos deben
ser públicos. Mientras el dueño no publique una versión notarizada, la app indica que el canal
todavía no tiene una actualización. No se publica una versión de prueba para simular este paso.

Los subcomandos `secure-update-check`, `secure-update-prepare --confirm-install REVISION` y
`secure-update-apply --confirm-install REVISION` sirven al flujo gráfico. Solo se ejecutan desde
el daemon congelado del bundle y derivan de allí sus rutas y clave pública; no aceptan una URL
de descarga proporcionada por el cliente.

Para la comprobación manual de archivos descargados se conserva la interfaz anterior.
Primero se inspecciona sin modificar la app:

```bash
~/Applications/Jarvis.app/Contents/Resources/Daemon/jarvis-daemon \
  secure-update-verify /ruta/Jarvis.update.json \
  --archive /ruta/Jarvis.zip \
  --release-manifest /ruta/Jarvis.release.json
```

La instalación exige escribir explícitamente la revisión Git de 40 caracteres mostrada por la
verificación:

```bash
~/Applications/Jarvis.app/Contents/Resources/Daemon/jarvis-daemon \
  secure-update-install /ruta/Jarvis.update.json \
  --archive /ruta/Jarvis.zip \
  --release-manifest /ruta/Jarvis.release.json \
  --confirm-install REVISION_DE_40_CARACTERES
```

Si el proceso se interrumpió fuera del flujo normal, la recuperación determinista es:

```bash
~/Applications/Jarvis.app/Contents/Resources/Daemon/jarvis-daemon secure-update-recover
```

## Límites honestos

Las pruebas unitarias ejercitan firmas adulteradas, replay, downgrade, sustitución de clave, swap,
rollback y recuperación sin voz. La certificación comercial real encadena P10/P11/P12/P13 y por ello
permanece pendiente hasta completar la prueba física de voz del propietario y disponer de Developer
ID y credenciales de notarización. P14 no finge esos prerrequisitos.
