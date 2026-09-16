# Distribución — ingeniería y validación sin voz

Fecha: 2026-09-14. Alcance: empaquetado local, verificación y preparación de releases. No incluye
publicación, instalación de la app ni cualificación vocal del propietario.

## Mejoras implementadas

- Dos comandos explícitos: `release_macos.sh preflight` y `release_macos.sh validate`.
- Construcción privada, independiente de `/private/tmp/Jarvis.app` y del ZIP previo en `dist`.
- Release autocontenida, firma ad-hoc local, licencia e instrucciones en español dentro del bundle.
- Verificación del ZIP antes de extraerlo; firma estricta de la copia extraída, todos los Mach-O,
  arquitectura arm64 y ausencia de dependencias absolutas de Homebrew/checkout en sus enlaces.
- Clave pública de actualización idéntica al pin del repositorio; daemon comprobado con HOME vacío.
- Huella de las entradas reales de compilación, incluidos archivos nuevos y eliminados. Cambios
  durante la construcción invalidan el informe; las fuentes sucias nunca se certifican como limpias.
- Staging también para `candidate`, `archive` y `notarize`. Requisitos de fuentes y toolchain se
  revisan antes de construir; firma, ZIP y evidencia antes de reemplazar artefactos en `dist`.
- Promoción local con exclusión mutua, manifiesto al final, copia verificada en el filesystem de
  destino e historial del conjunto anterior. Los fallos probados restauran todos los artefactos.
  Evidencia de canal/notarización obsoleta se conserva en el historial, no junto al ZIP nuevo.
- Validador común para empaquetado, actualización y P12/P13: colisiones Unicode/mayúsculas, rutas
  peligrosas, metadatos especiales, tamaños, cifrado, enlaces, ciclos y componentes privados.
- Resolución real de enlaces de frameworks a través de `Versions/Current`, sin normalización
  léxica incorrecta ni extracción a través de enlaces. La prueba del paquete real permitió
  detectar y eliminar la antigua comprobación duplicada que rechazaba esos alias legítimos.
- `check` inspecciona el ZIP autenticado, no un bundle temporal posiblemente obsoleto.
- Rechazo de modos de build inválidos antes de cerrar procesos y del uso de directorios de
  empaquetado reutilizados/enlazados. El congelado del daemon incorpora un lock de construcción.

## Pruebas

| Validación | Resultado |
|---|---|
| Distribución, ZIP, evidencias, actualización y promoción | 100 aprobadas |
| Suite Python completa | 1.697 aprobadas, 17 omitidas |
| Suite SwiftPM nativa | 266 aprobadas en 15 suites |
| Ruff de `src`, `tests` y scripts modificados | Correcto |
| Sintaxis Bash y `git diff --check` | Correcto |
| Rechazo temprano de `candidate` con fuentes sin commit | Correcto; ZIP previo intacto |

Los primeros intentos dentro del sandbox fallaron por restricciones de sockets/servicios nativos y
generación de dSYM. Las ejecuciones autorizadas fuera del sandbox completaron las suites. No se
rebajaron aserciones para convertir fallos ambientales en aprobaciones.

El ZIP anterior de `dist/Jarvis.zip` conservaba 49.117.449 bytes y coincidía con el SHA-256 de su
manifiesto original, correspondiente a `7c8bb1c615de7a368cec38cb3bf39a83624186bb`.

## Candidato final verificado

Conservado en `dist/validation/2026-09-14-7d030aa04cc8/`, separado de la release anterior:

- [Jarvis.zip](../../dist/validation/2026-09-14-7d030aa04cc8/Jarvis.zip): 153.833.942 bytes
  (aproximadamente 147 MiB); 413.064.019 bytes sin comprimir.
- [Evidencia de validación](../../dist/validation/2026-09-14-7d030aa04cc8/Jarvis.validation.json):
  `status=passed`, **223 Mach-O verificados**, autodiagnóstico del daemon **8/8**, cero llamadas de
  red y cero escrituras persistentes durante ese autodiagnóstico.
- [SBOM SPDX](../../dist/validation/2026-09-14-7d030aa04cc8/Jarvis.spdx.json).
- SHA-256 del ZIP:
  `5be5d49f1942ceda857ae4d9778421cdb541e9266a6b2d7b17b51394781cbac2`.
- Huella de entradas de compilación:
  `7d030aa04cc8b37e51cd6c0cd9e409aca8270e7a892bb1c62151783321b7118b`.

Perfil **ad-hoc-local-validation**, fuentes con cambios locales identificados, revisión base
`eed2427ce047ce8402db6d91d375611a0b087f6c`. `public_release_qualified=false` es deliberado:
este paquete prueba ingeniería de distribución; no es una versión pública ni una certificación
biométrica. El bundle ocupó 404.156 KiB frente al presupuesto de 450.560 KiB.

Log técnico de la construcción final: `/private/tmp/aegis-distribution.ojzc_t23/build.log`.

## Pendientes explícitos

1. **Voz del propietario:** no se grabó, leyó ni solicitó. P10 y la evidencia vocal de P11 permanecen
   pendientes hasta una autorización posterior explícita.
2. **Interacciones físicas de macOS:** no forman parte de esta validación no interactiva.
3. **Publicación:** no hay Developer ID Application disponible en este host; la consulta autorizada
   solo encontró la identidad `Jarvis Local Development`. El toolchain activo es Command Line Tools
   y no tiene Metal para el perfil público completo que incluye los helpers Swift MLX.
4. **Compuertas comerciales:** no se ejecutaron P11/P12/P13/P14 físicos ni se enviaron artefactos a
   Apple. No se consultaron secretos de notarización, se firmó un canal o se publicó una versión.
5. **Instalación:** no se sustituyó, reinició ni abrió la app instalada; no se cambiaron LaunchAgents
   ni permisos. No se creó commit ni se hizo push.

Una interrupción abrupta durante promoción puede requerir recuperar el conjunto anterior desde
`dist/.release-previous.*`; no se promete atomicidad de varios archivos ante pérdida de energía.
El lock y la autenticación por hashes impiden tratar un conjunto incompleto como una release válida.

## Criterio aplicado

Las guías `packaging-notarization`, `signing-entitlements` y `swiftpm-macos` orientaron la separación
entre validación local y publicación, la inspección del artefacto realmente empaquetado y las
pruebas nativas. Contrato técnico: [ADR-0225](../architecture/ADR-0225-distribution-without-voice.md).

Referencias oficiales: [empaquetado de macOS](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution)
y [flujo de notarización](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow).
