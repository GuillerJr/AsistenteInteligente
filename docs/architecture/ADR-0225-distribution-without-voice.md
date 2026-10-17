# ADR-0225: distribución verificable sin activar pruebas vocales

- Estado: aceptado
- Fecha: 2026-09-14

## Contexto

El propietario solicita completar la ingeniería de distribución y aplazar cualquier prueba que
requiera su voz hasta una autorización posterior. P10/P11/P12 siguen exigiendo sus evidencias
reales: no es correcto omitirlas y presentar el resultado como una release comercial aprobada.
Además, el empaquetado anterior compartía `/private/tmp/Jarvis.app` y podía reemplazar el ZIP antes
de descubrir un árbol fuente sucio o un fallo posterior de firma/evidencia.

## Decisiones

1. `release_macos.sh preflight` inspecciona requisitos locales sin compilar ni acceder al micrófono,
   Keychain o archivos del propietario. No realiza llamadas al proveedor.
2. `release_macos.sh validate` construye Release con firma ad-hoc en un workspace nuevo, privado,
   bajo `/private/tmp/aegis-distribution.*`. Excluye modelos personales y los helpers Swift MLX
   opcionales; no cambia NVIDIA como proveedor de conversación/voz. El runtime Python sí conserva
   las dependencias locales usadas por reconocimiento de voz y memoria.
3. El ZIP se inspecciona antes de extraerlo. La copia extraída debe superar firma estricta, revisión
   de todos sus Mach-O (arm64, firma y bibliotecas ajenas al host), clave pública fijada y el
   `--self-test` del daemon con HOME aislado. No se abre la interfaz ni se instala un LaunchAgent.
4. La evidencia `Jarvis.validation.json` registra hashes de las entradas de compilación, incluyendo
   archivos nuevos y eliminados, hashes del ZIP y sus miembros, SBOM y resultados reales. Rechaza
   cambios en las entradas durante la construcción. El perfil local admite un checkout sucio,
   explícitamente señalado, pero no genera `Jarvis.release.json` ni aprueba ninguna compuerta pública.
5. `candidate`, `archive` y `notarize` exigen fuentes limpias **antes** de compilar, incluyendo
   archivos de compilación no versionados. Construyen en staging privado y promocionan a `dist`
   únicamente después de verificar. El conjunto anterior —incluidos canal y log de notarización—
   se conserva en `dist/.release-previous.*`; un error durante el intercambio restaura lo anterior.
   El manifiesto se instala al final; los lectores verifican hashes y fallan de forma cerrada si
   observan un intercambio en curso. No se afirma atomicidad de varios archivos ante pérdida de
   energía: una interrupción abrupta puede exigir recuperación desde el historial y deja el lock
   como indicación de que no debe iniciarse otra promoción sin inspección.
6. `check` valida la app extraída del ZIP autenticado, nunca un bundle temporal ajeno. Solo el perfil
   notarizado ejecuta además `stapler validate` y Gatekeeper. Firma válida no significa que P12/P14
   hayan quedado aprobados.
7. Un validador ZIP común protege empaquetado, actualización y cualificaciones P12/P13. Rechaza
   colisiones de mayúsculas/Unicode, rutas no canónicas, enlaces usados como directorios de
   extracción, ciclos, escapes, destinos inexistentes, archivos especiales, bits privilegiados,
   payloads de enlaces sin límite, datos cifrados y componentes privados conocidos. Acepta los
   enlaces internos legítimos de frameworks. Ningún lector extrae primero y valida después.

## Fronteras que se conservan

- Pruebas vocales del propietario: `awaiting_explicit_user_authorization`.
- Interacciones físicas con macOS: no ejecutadas por la validación no interactiva.
- Developer ID, notarización Apple y canal Ed25519: compuertas independientes, no simuladas.
- Ninguna instalación, reinicio, cambio de permisos, firma de canal ni publicación se deriva de
  ejecutar `preflight` o `validate`.
- El ZIP incluye licencia e instrucciones de instalación en español. La SBOM es un inventario de
  dependencias bloqueadas; no se presenta como auditoría de licencias ni de vulnerabilidades.

Referencias de distribución Apple: [empaquetado de software macOS](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution)
y [personalización de la notarización](https://developer.apple.com/documentation/security/customizing-the-notarization-workflow).
