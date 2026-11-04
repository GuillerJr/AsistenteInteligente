# Seguimiento OC-26: extracción PDF y visión

El 4 de noviembre el dueño pidió contrastar el fallo OCR de Jarvis con la
implementación de OpenClaw. La revisión OC-26C había cubierto la entrada y el
contrato de PDF, pero no el motor del plugin `document-extract`. PDFKit y Vision
eran decisiones propias de Jarvis; no acreditaban equivalencia del motor.

## Identidad y evidencia

Referencia de sólo lectura: OpenClaw `1e3ff306abaff29f03e7668d53b01c07f10294ac`.
Se adquirieron diez blobs Git (44.245 bytes) y se contrastaron con los archivos
de ese clon. La partición inerte quedó en
`/private/tmp/jarvis-openclaw-pdf-rea-20261104`, con manifiesto de rutas/tamaños y
SHA-256. No se ejecutó, instaló, compiló ni modificó OpenClaw. Sus fuentes no se
incorporaron al producto ni a este repositorio.

REA analizó nueve rutas seleccionadas: siete TS y dos manifiestos JSON, 43.460
bytes de texto, sin fallos de parseo. El README se leyó por separado. El
[resumen saneado](qa/openclaw-pdf-engine-20261104/REA_SUMMARY.json) conserva las
identidades y cobertura. El padre es
`ev_fe7be8c0f4004891b7c2345aebb30c60448bbb52566ee0c2ebbbaf69aa149d25`;
la traza de `clawpdf` es
`ev_0fe17eb28c8510acc3dd704da1626c4feb0374885c5b8b5ccdcd2ad2c3b894ee`.
Es análisis estático de una partición de fuentes, **no** prueba de un paquete
distribuido o de ejecución. La etiqueta genérica de autoridad del productor REA
no cambia ese origen. Quedan una incógnita de aplicación y 970 semánticas;
`clawpdf` y los SDK/proveedores no están incluidos ni ejecutados.

| Fuente del snapshot | Bytes | SHA-256 |
| --- | ---: | --- |
| `extensions/document-extract/document-extractor.runtime.ts` | 6837 | c3130b2b9a2f25c10acaee673a802142ef53dff1ee2981dd0273a88be89eac0f |
| `extensions/document-extract/document-extractor.ts` | 1341 | 1e21129e3bf4c37c666e1bde714aa44c73e79b7f0a205b48d62187a1965a54b5 |
| `extensions/document-extract/document-extractor.worker.ts` | 1288 | 27a6ac71ed6f411bc68df5055653adc633bf743ec180479751d72b92a5024fa5 |
| `src/agents/tools/pdf-tool.ts` | 23360 | e617f710a147e4b5d08055c5cfb3527aa0c4d7917acab1ac6735b4d046e99763 |
| `src/agents/tools/pdf-native-providers.ts` | 9083 | aef844c00283da0f34422ce3d7da28e7f690ceff74f5576b57339c880699e28a |

## Recorrido observado en las fuentes

1. El plugin declara `clawpdf 0.3.2`. Su runtime llama `createEngine()`, abre el
   PDF y obtiene `page.text()`. Sus comentarios y README identifican PDFium;
   no se inspeccionaron los binarios internos de esa dependencia.
2. Si el texto de una página no alcanza el umbral, la renderiza como PNG.
   Devuelve texto, imágenes y metadatos de páginas/truncamiento. Renderizar
   una página no equivale a realizar OCR.
3. `pdf-tool.ts` obtiene esas extracciones (líneas 277–288), comprueba si el
   modelo acepta imágenes (479–500) y entrega el contexto al proveedor
   configurado (503–528). También tiene envío PDF directo a Anthropic/Google
   cuando el proveedor lo soporta (434–475). «Nativo» en esa rama significa
   soporte PDF del proveedor, no Vision de Apple.
4. Si no existe texto y el modelo no admite imágenes, rechaza la solicitud.
   Si existe texto pero no soporte visual, puede omitir los PNG y declara
   `imagesTruncated`. El análisis de imágenes requiere las credenciales
   normales del proveedor; no es OCR local sin modelo.
5. El extractor usa un worker singleton y plazo de 180 segundos, cancelación,
   selección de páginas, presupuesto agregado de píxeles, máximo de arista
   de 10.000 píxeles y 200.000 caracteres. Sus dependencias y llamadas
   dinámicas siguen siendo fronteras no acreditadas por este estudio.

## Diferencia con Jarvis y estado de la corrección

`NativeMediaExecutorDecode.swift` llama a `VNRecognizeTextRequest` con nivel
`.accurate` antes de entregar la imagen o la hoja de contacto. El fallo de OCR
se convierte en `media_native_decode_failed` y corta también la observación
visual. `NativeMediaExecutorAV.swift` tiene la misma dependencia para los
cuadros muestreados. Jarvis ya dispone de entrega de imágenes aprobadas al
cliente oficial Codex en `bridge.py`/`codex.py`; una aceptación del transporte
no acredita por sí sola consumo por el modelo.

Por tanto, la capacidad PDF/imagen de OpenClaw existe, pero no demuestra que
su camino ejecute ni resuelva la llamada Apple que falla aquí. La separación
entre extracción local, OCR local y análisis visual es una mejora de diseño
fundada en la referencia. El dueño eligió después adoptar ese reparto. La
[implementación propia](PHASE_A_MEDIA_VISUAL.md) retira el OCR de la ruta
principal, conserva texto embebido y previsualizaciones aprobadas y separa la
interpretación del modelo. Su cualificación se registra aparte; esta lectura
estática no la acredita.

Las 26 pruebas de medios pasaron en el Mac físico 27.2 sin el aislamiento
Seatbelt agregado. Con las mismas fuentes, el control Seatbelt falló ocho
afirmaciones. En CI macOS 27.0.1, tanto el SDK predeterminado como la
[comparación SDK 27.2](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/41897239819)
fallaron en OCR preciso; la comparación independiente rápida sí reconoció el
marcador. La causa interna del fallo Apple remoto **no está establecida**.
No se atribuye a intercambio de modelos ni se declara resuelta por cambiar SDK.

La comparación 27.2 usó realmente Xcode 27.2 build 27B48, Swift 6.4.2 y SDK
27.2 sobre macOS 27.0.1. Ejecutó 26 casos con siete fallos; el supervisor cerró
normalmente, conservó fuentes y midió 696.344.576 bytes de pico en 135,847 s.
Su log acotado tiene SHA-256
`dc983bc1e95f13ed5abccad92b8ae2fc919ec75a6992bd9052cc221aa11d2ee9`.

## Comparador de CI y verificación focal

El workflow manual admite ahora únicamente los runners oficiales `xcode-27`
y `macos-26`, con mínimo de SO/SDK 27 por defecto y 26 sólo por selección
explícita. Una combinación desconocida se rechaza antes de dependencias. No
cambia la CI principal, los criterios OCR, cuotas ni omite pruebas. Comparar
el entorno previo es un diagnóstico separado de corregir el diseño del producto.

Pasaron 58 pruebas focales de selección Bash y admisión del entorno en 2,16 s
(supervisor 3,008 s; pico 3.690.496 bytes; cuota APFS y cierre confirmado).
Recibo `vx41990c`, SHA-256
`3b05651d937dd4472bd0e3d479a00de4dc0fecde481af69d3fe486839a348901`.
Las fuentes fueron idénticas antes/después: HEAD `52e4924`, fingerprint
`29692e59cc996da1f490ff5d126cfd166cf7fddbd3dd255c88d44f045d976a66`.
El intento previo `sns4qko0` cerró sin preparar cuota ni ejecutar tests.
Las fuentes estáticas y las pruebas focales no sustituyen la CI completa del
commit publicado ni cierran la verificación nativa pendiente.

Este seguimiento amplía el recorrido OC-26 sin presentar una revisión
exhaustiva ni recalificar las familias de la matriz. La fase A sigue abierta.
