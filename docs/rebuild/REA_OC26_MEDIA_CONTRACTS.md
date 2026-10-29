# REA OC-26C/D — contratos propios de documentos y multimedia

Referencia de sólo lectura: OpenClaw
`1e3ff306abaff29f03e7668d53b01c07f10294ac`.
La adquisición por blobs Git y el análisis REA se realizaron por particiones
fuera del producto, en `/private/tmp/jarvis-channel-media-rea-20261029`.
No se ejecutó OpenClaw, no se importó su runtime y no se incorporaron sus fuentes.

## Identidad y búsqueda acotada

El manifiesto raíz fija rutas, tamaños y hashes. Los Evidence IDs padre recibidos
de la sesión REA central son:

- OC-26C: `ev_4dccb9ad08b367a570843daffa09d4a5333b6b5c58fc38c38c90a4c8958983c7`.
- OC-26D: `ev_395caa999f30ede6a572be5bc0aa684dec7e2a28cb02aae491df1b28b7f509aa`.

| Parte / ruta de referencia | Bytes | SHA-256 |
| --- | --- | --- |
| 26C `src/media/input-files.ts` | 14619 | 5edbdd6526c6e11e4dbd7c6d36cd4f05d292aed53966fc3a876f477721650e32 |
| 26C `src/media/pdf-extract.ts` | 4354 | 684c1f53ee79cf171fb19678fb52fddbb84a4bc4cd80200a153437b59cbb6841 |
| 26C `src/media/attachment-processor.ts` | 1252 | 45292b374b9727df8df86490b7cb6e177ccd44a6540d82cfe74ea5ea793739aa |
| 26C `src/media-understanding/file-extraction-limits.ts` | 2020 | 2976f9751e0aeefa8bca1e70aaeecd1218c577c1dc843f36c71eef453b7b8602 |
| 26D `src/media/audio.ts` | 1271 | 020dc441363e992ec73c53d6681babcc6ff8047d48521fb7f14ed5b152f01e9e |
| 26D `src/media/image-processor.ts` | 4268 | 8d69a9434c0deeb2ccf7332b3014d9761a08be453776f8878dbd993caa95e72f |
| 26D `src/media-understanding/local-audio.ts` | 12995 | d0b661da7f01f5ae4389f207839a1308fb32b6941908d07c884a6d3190c858df |
| 26D `src/media-understanding/file-attachment-outcomes.ts` | 7630 | 9f35409ecba7d98fe8b24210584385e0cc24406589bcadb2028e28604696fd3c |

Son ocho fuentes seleccionadas (48.409 bytes), no un censo semántico de toda la
referencia. El grafo REA conserva 515 incógnitas semánticas en C y 552 en D,
además de dependencias externas no resueltas. Los hashes identifican la lectura;
no demuestran que una dependencia o un formato funcionen en el Mac del dueño.

## Preguntas, observaciones y decisiones propias

| Pregunta | Observación estática acotada | Contrato propio en Jarvis |
| --- | --- | --- |
| ¿Cuándo se considera aceptado un archivo? | La entrada distingue tipo, origen, límites de bytes y datos de extracción; una descarga cancelada conserva cierre del transporte. | El host autoriza importación/transmisión por separado; manifiesto inmutable de sesión/workspace/hash y guard antes de publicar. No hay fetch genérico desde una URI de documento. |
| ¿Un máximo de páginas acredita documento completo? | PDF valida páginas procesadas, selección, unicidad y truncamiento antes de describir completitud. | Citas por fuente/página/cuadro y truncamiento explícito; una respuesta nativa fuera de hash/tipo/intervalo no se convierte en observación válida. |
| ¿Basta con aceptar el MIME del remitente? | Se contrastan tipo detectado y tipo anunciado, junto a la normalización de imágenes y límites posteriores. | `sniff_media` contrasta cabeceras/estructura con el MIME específico; el worker valida decodificación/píxeles y conserva el hash original frente a cualquier derivado. |
| ¿Un adjunto de audio implica transcripción o envío? | El catálogo de formatos, la disponibilidad de motores locales y los resultados de adjuntos son capas distintas. | Ingestión sólo conserva bytes/metadatos. STT, visión y reproducción son acciones explícitas; codec local y worker no son permiso para inferencia o publicación. |
| ¿Cómo expresar un fallo de extracción? | Los resultados de adjuntos distinguen fuente, contenido producido y motivos de fallo o límite. | Errores estables, `requires_native`, ausencia de texto observado y recibos de efecto incierto sin replay. No se sustituye una observación fallida por texto inventado. |

Estas son decisiones propias informadas por lectura y grafo; no una traducción
línea a línea. Las implementaciones Python usan sus propios contratos, stdlib y
el codec local elegido. PDFKit, Vision, Speech y AVFoundation se verifican por
sus trabajadores nativos separados; la referencia no se ejecuta como backend.

## Lo que esta investigación no acredita

No valida todos los codecs, plugins de extracción, proveedores, páginas PDF,
archivos hostiles, motores OCR o dispositivos de salida de la referencia.
No habilita generación de imagen/música/vídeo, ejecución de macros, lectura de
cuentas, enlaces externos ni importación del historial del dueño. La aceptación
productiva se registra con CI y recorridos nativos propios en el cierre integrado,
no mediante contar los nodos o incógnitas de este grafo.
