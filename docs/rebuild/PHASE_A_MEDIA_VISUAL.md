# Extracción de texto y observación visual

Decisión del dueño, 4 de noviembre: usar el recorrido de OpenClaw en este
punto y retirar el OCR Apple como requisito previo. La implementación propia
prepara texto embebido y píxeles localmente; Codex con ChatGPT interpreta la
vista tras la aprobación del archivo exacto. No incorpora otro proveedor ni
envía audio para transcribir.

## Referencia y diferencia retirada

El [seguimiento OC-26](REA_OC26_PDF_ENGINE_FOLLOWUP.md) documenta el plugin
extractor: OpenClaw extrae texto con `clawpdf` y renderiza páginas para un
modelo con visión. Jarvis conserva ese reparto con código propio y
PDFKit/ImageIO/AVFoundation, sin importar OpenClaw, su plugin ni `clawpdf`.

REA conserva el padre
`ev_fe7be8c0f4004891b7c2345aebb30c60448bbb52566ee0c2ebbbaf69aa149d25`,
la traza `ev_0fe17eb28c8510acc3dd704da1626c4feb0374885c5b8b5ccdcd2ad2c3b894ee`
y la vista exacta del módulo
`ev_1c400bc5cea1bcec69cbcb7476997e13d50bd7a3fc1c071707a030e11c81c2e5`.
Son fuentes inertes del snapshot fijado, no ejecución de proveedores ni
inspección de los binarios internos de PDFium.

La comparación anterior `f6b0bf8` aprobó 26 casos OCR/medios en
[macOS 26.6.2 / SDK 26.5](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/41900301596),
Xcode 26.6 build 17F113, Swift 6.3.3: 7,208 s de tests, supervisor 134,045 s,
pico 618.635.264 bytes y cierre confirmado. Las ejecuciones macOS 27.0.1
conservaron el fallo preciso. Esto diferencia entornos, sin identificar la
causa interna Apple. La CI principal sigue en macOS 27; no se cambió de SO
para aprobar el producto nuevo.

## Comportamiento

| Entrada y acción | Preparación local | Entrega al modelo |
| --- | --- | --- |
| PDF, `extract` | Texto embebido de páginas seleccionadas y mosaico | Texto citado y vista aprobada; páginas escaneadas con texto local vacío |
| Imagen, `extract` | Previsualización del primer fotograma | Imagen aprobada; sin segmentos OCR |
| Vídeo, `extract` | Tres muestras con instantes reales | Mosaico con regiones/citas; siempre parcial |
| Audio/vídeo, `transcribe` | Reconocimiento local del idioma instalado | Transcripción citada; sin frame ni audio enviado al proveedor |
| Texto, Office o RTF | Extracción inerte existente | Texto citado; sin macros ni enlaces ejecutados |

`embedded_text_and_visual_v1` es obligatorio en el despacho y resultados
visuales. Un host antiguo o un resultado que afirme OCR no se admite como
este recorrido. `ocr_performed=false`, `local_text_extraction`,
`visual_required` y `visual_analysis=model_required` separan preparación e
interpretación. Las regiones incluyen locator, fila/columna y coordenadas
normalizadas desde arriba a la izquierda.

El frame es obligatorio para imagen/PDF/vídeo. Ausencia, JPEG mayor de 48 KiB
o desborde del sobre firmado de 128 KiB producen fallo explícito; no éxito
sin píxeles. Se comprueban hash, JPEG, base64 y correspondencia de citas.
La vista sólo viaja una vez por RAM; sus bytes no aparecen en recibos durables.
Se mantienen ID/SHA, autoridad sesión/run/operación/argumentos, revalidación,
cancelación con cierre, límites y rechazo de PDF cifrado/referencias externas.
`verified` acredita preparación/transporte; `content_truth_verified=false`
conserva que la interpretación del modelo no es texto local verificado. Un
ACK de Codex acredita aceptación, no consumo demostrado por sí solo.

## Pruebas y cualificación

Pasaron **78 casos focales Python**, con dos de codec reservados a CI, en
3,15 s. Supervisor 4,087 s, pico 3.862.528 bytes, APFS, grupo cerrado, cuota
desmontada y fuentes idénticas. Recibo `dlxlti0p`, SHA-256
`d377bd9785b79556531d2216a8f04632421fd9115f51d553abe7f5745742ea66`;
fingerprint `92e8fddd4e9846380c0554af479e23ea8eff78dcd239df1e6253512da54e28b6`.

Se conservan los intentos anteriores: `56osg3jv` falló en colección por
`soundfile` ausente; `3ibh67b1` fue rechazado por sintaxis de admisión;
`b_3hb5ie` aprobó 64 casos y rechazó 14 setups que querían crear perfiles
fuera de cuota. El fixture se corrigió para usar el temporal supervisado en
local y mantener sockets cortos en CI. No se ampliaron permisos ni instalaron
dependencias. Esos intentos no son suites aprobadas.

Las regresiones cubren worker antiguo/política, texto inventado en imágenes,
páginas/regiones equivocadas, frame ausente o inválido, cancelación tardía y
entrega única sin persistencia. Los tests Swift reales cubren PDF embebido y
escaneado, PNG, colores de cada región del JPEG, vídeo muestreado, transcripción
sin preparación visual, cifrado y límites/autoridad/limpieza.

Los experimentos y requisitos OCR retirados pertenecían al contrato anterior;
sus fuentes y fallos se conservan en Git e informes. No se etiquetan como OCR
aprobado. Compilación Swift, suites completas, paquetes y smoke se ejecutan
en CI remota para el SHA publicado. Su cualificación y el recorrido nativo
con una imagen sintética y Codex real siguen pendientes en este checkpoint.

Este cambio aborda el punto de documentos/visión elegido; no completa todas
las familias de fase A ni inicia la fase B.
