# ADR-0011: Menu Bar App mínima y explícita

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** solicitar TCC desde el helper CLI era frágil; Tauri/Electron no aporta valor para
   permisos ni estado del daemon.
2. **Eliminar:** se excluyen HUD, chat, login item, preferencias, telemetría UI y dependencias
   externas.
3. **Simplificar:** SwiftUI `MenuBarExtra`, tres archivos Swift y un único salto AppKit a Ajustes del
   Sistema.
4. **Acelerar:** SwiftPM sigue siendo la única compilación; no se crea proyecto Xcode.
5. **Automatizar:** un script detiene, compila, empaqueta, firma, lanza y verifica el proceso.

## Decisión

`Jarvis` es un producto SwiftPM nativo y menu-bar-only (`LSUIElement=true`). Mantiene un modelo
app-wide que consulta `health` cada diez segundos mediante `AegisAudioCore`. El icono distingue
conectado, desconectado y fallo de seguridad.

Los permisos de micrófono y Speech solo se solicitan al pulsar sus botones. Si TCC ya decidió, la
acción abre directamente el panel de Privacidad correspondiente. Arrancar o monitorizar la app no
inicia captura, no instala assets y no cambia TCC.

No se habilita App Sandbox: el cliente actual necesita UDS y `/usr/bin/security`. Una decisión
posterior añadió los dos entitlements exigidos por TCC al firmar con hardened runtime: entrada de
audio y Apple Events. El `Info.plist` declara las descripciones de uso obligatorias. Al no
existir una identidad de desarrollo instalada, el script usa firma ad hoc; una identidad real puede
inyectarse con `AEGIS_CODESIGN_IDENTITY`.

FileProvider añade `FinderInfo` a bundles creados dentro de Documents e invalida `codesign --strict`.
Por eso el `.app` ejecutable se ensambla en `/private/tmp` y `dist/` conserva un ZIP sin xattrs. Esta
decisión elimina una carrera de metadatos en lugar de añadir reintentos.

## Encaje en el roadmap

- **Fase 3:** cierra la superficie explícita de permisos para audio y Speech.
- **Fase 5:** establece el daemon visual mínimo y voice-first; la esfera HUD 3D continúa aplazada
  hasta que el flujo de voz real funcione con permisos concedidos.

## Consecuencias

La app puede operar siempre en segundo plano con coste mínimo y sin una ventana visible. La firma ad
hoc sirve para desarrollo local, no distribución. Notarización, login item y HUD requieren hitos
separados y no se adelantan en este corte.
