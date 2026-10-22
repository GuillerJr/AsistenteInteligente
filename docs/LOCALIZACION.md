# Textos de la interfaz

La fuente es `native/AegisAudio/Sources/AegisMenuBar/Resources/Localizable.xcstrings`,
con español como idioma base. Los textos conocidos de componentes, estados y avisos
usan `String(localized: …, bundle: .module)`. Los literales SwiftUI también se extraen.
El contenido del dueño y las respuestas del modelo se muestran sin traducirlos como
claves. No localices identificadores IPC, nombres de herramientas ni campos firmados.

Para añadir o cambiar textos:

```bash
./script/test_native.sh
.venv/bin/python script/sync_localizations.py --build-root /private/tmp/aegis-menubar-build
.venv/bin/python script/sync_localizations.py --check
```

El compilador genera `.stringsdata` con los tipos correctos de las interpolaciones
(por ejemplo `%lld` para enteros). El script conserva las traducciones ya existentes
y genera `es.lproj/Localizable.strings` para SwiftPM. El empaquetado incluye ese recurso
en el bundle de SwiftPM y en el bundle principal para las vistas de SwiftUI y AppKit.
Confirma el catálogo y el recurso generado juntos. Una recompilación posterior aplica
las traducciones al bundle. No añadas una copia independiente de textos a mano.

Para introducir otro idioma, añade su localización al catálogo y genera sus recursos
con `xcrun xcstringstool compile … --output-directory …`; incorpora el nuevo `.lproj`
al target y al empaquetado. La interfaz inglesa no forma parte del alcance de la 1.0.
