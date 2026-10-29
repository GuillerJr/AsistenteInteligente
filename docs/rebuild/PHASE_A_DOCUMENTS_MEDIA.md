# Documentos, adjuntos y multimedia de Jarvis

Estado del componente: `implemented_core_awaiting_remote_and_native_qualification`.
Este contrato registra código propio y pruebas añadidas; no presenta sus fixtures
como cualificación real ni adelanta el cierre que debe consolidar el host.
El destino elegido es macOS Apple Silicon con Codex con ChatGPT. No incorpora
generación de imagen/música/vídeo, otros proveedores ni inferencia NVIDIA.

## Entrada y propiedad

`media_attachments.py` publica originales inmutables en
`.jarvis-attachments/<session_id>/` dentro del workspace seleccionado. Cada
`AttachmentManifest` fija identificador, sesión, workspace, nombre, MIME detectado,
tipo, tamaño, SHA-256 y procedencia. El identificador de importación es idempotente
únicamente para los mismos bytes y metadatos. Un cambio produce conflicto.

Los bytes no se incorporan como blobs SQL ni como rol de respaldo del perfil.
El respaldo y la restauración de archivos de workspace siguen siendo operaciones
separadas. Un registro restaurado o un archivo ausente no autoriza búsquedas,
reimportaciones ni lecturas de otra carpeta. Cambiar la identidad de workspace
retira el acceso del ámbito anterior.

Los adjuntos temporales permanecen en RAM. Limpiar su sesión o cerrar el store
retira ese acceso y los bytes RAM; no crea un manifiesto ni blob en disco.
Revocar un original persistente escribe una marca durable de ámbito/hash exactos:
conserva el original y bloquea lecturas futuras. Los originales revocados cuentan
en las cuotas; no se borran silenciosamente para poder importar más.

La carpeta privada debe permanecer inaccesible a herramientas de archivos
ordinarias. El host valida sesión/workspace/ejecución/operación, muestra la revisión
del contenido elegido y vuelve a comprobar su autoridad antes de publicar o
transmitir. El store no convierte nombres, hashes, rutas de Telegram ni contenido
de un documento en consentimiento.

`media_sources.py` conserva el recorrido por descriptores del workspace, sin
symlinks, hardlinks ni FIFOs; compara identidad/tamaño/tiempos y SHA-256 antes de
entregar la fuente. No habilita lecturas de credenciales, historial o fuera del
workspace. El compositor nativo debe comprobar también el origen elegido antes
de leer sus bytes: una selección de archivo no constituye una excepción automática
a las reglas del dueño sobre datos privados.

## Formatos y observación

| Entrada | Tratamiento local |
| --- | --- |
| UTF-8, Markdown, JSON, YAML y XML de texto | Texto inerte con hash de los bytes originales y cita de documento; límites explícitos de salida. |
| HTML | Texto visible; scripts, estilos, plantillas y cabeceras excluidos. Los enlaces, imágenes y referencias externas no se descargan ni ejecutan. |
| CSV/TSV | Filas citadas por separado; las fórmulas permanecen como datos. No se abren hojas en otra aplicación. |
| DOCX | Texto por páginas lógicas definidas por saltos explícitos. Se declara que esas cifras no son páginas renderizadas de Word. |
| XLSX/PPTX | Texto/valores almacenados de partes XML con citas de hoja/diapositiva y metadatos de parte fuente. No se evalúan fórmulas, macros ni relaciones externas. |
| PDF, imágenes, RTF, audio y vídeo | El núcleo devuelve un plan de observación nativa y `requires_native=true`, sin texto observado inventado. PDFKit/Vision/Speech/AVFoundation y reproducción se conectan mediante el broker del host. |

El tipo se detecta por cabeceras y formato, no por una afirmación de MIME del
remitente. Una afirmación específica contradictoria se rechaza. La extracción
Office valida entradas ZIP, rutas, tamaños, relación de compresión, XML sin DTD
ni entidades y ausencia de macros. No extrae ni ejecuta objetos incrustados.

`MediaDocument.public()` distingue hash y citas comprobables de verdad del
contenido: siempre conserva `external_content=true` y
`content_truth_verified=false`. Su texto no cambia instrucciones ni permisos.

## Respuesta del analizador nativo

`complete_native_document` recibe sólo el resultado textual de un worker que el
host ya autenticó. Exige esquema `jarvis.media.v1`, éxito/verificación conocidos,
hash/tipo exactos, ejecución local y segmentos con localizadores acotados.
Rechaza ámbitos discordantes, duplicados, texto excesivo, metadatos recursivos
o portadores de imágenes/base64. Al completar la observación retira el plan
pendiente; no lo vuelve a ejecutar por encontrar un resultado parcial.

Un frame opcional se retira antes de esa validación y sólo puede circular por el
canal efímero de imagen ya autorizado del host. No se escribe dentro de los
metadatos, SQL o texto de historial. Un modelo sin la capacidad necesaria debe
producir un error explícito; no justifica afirmar que vio el documento o imagen.

## Audio Ogg de Telegram y otros adjuntos elegidos

`media_audio.decode_ogg_to_wav` utiliza `soundfile==0.13.1` como codec local,
con artefactos fijados en `uv.lock`. No es un proveedor de inferencia. Comprueba
la versión y capacidad OPUS efectiva; no busca otro servicio ni descarga modelos.
La documentación primaria describe la distribución de libsndfile dentro de sus
wheels y el soporte de archivos en memoria:
[SoundFile 0.13.1](https://python-soundfile.readthedocs.io/en/0.13.1/).

La conversión conserva el Ogg original, valida su SHA-256 y decodifica bloques
de 4096 frames bajo guard, cancelación y plazo. Acepta OPUS/VORBIS, una o dos
entradas, 8–48 kHz y como máximo 120 segundos; deriva WAV mono PCM16 sólo en RAM.
El recibo distingue `input_sha256`, `derived_sha256`, versión real del codec,
duración, frames y canales de origen. El empaquetado de distribución debe
conservar la información de licencias de sus dependencias oficiales.

El worker de voz se autoriza sobre el hash del WAV derivado. El host conserva
la relación explícita con el hash del original y no sustituye el hash de una
respuesta firmada para hacerla pasar por otra fuente. La conversión no transcribe,
envía ni reproduce por sí sola. Una llamada C en curso no puede interrumpirse
forzosamente desde esta función: el host debe conservar al worker y su custodia
hasta confirmar el cierre.

## Límites y publicación incierta

| Recurso | Máximo predeterminado |
| --- | --- |
| Archivo original o WAV derivado | 16 MiB |
| Adjuntos retenidos por sesión | 32 MiB y 32 originales |
| Workspace, RAM y persistentes contabilizados juntos | 128 MiB y 128 originales |
| Adjuntos ofrecidos en una ejecución | 8, aplicado por el host |
| Texto extraído por documento | 48 KiB UTF-8 |
| Segmentos de extracción | 64; las llamadas nativas conservan además su intervalo explícito |
| ZIP Office | 128 entradas, 16 MiB descomprimidos, ratio máximo 100:1 |
| XML individual | 2 MiB, UTF-8, sin DTD/entidades |
| Imagen | 40 millones de píxeles; el worker limita también la salida visual |
| Audio/vídeo inspeccionado | 120 segundos bajo el presupuesto de worker |

La cuota persistente reserva espacio para una revocación futura. El catálogo se
bloquea de forma no bloqueante; un propietario que no termina no se sustituye ni
se presume muerto. La publicación verifica bytes, permisos y directorios.
Si se publicó el blob y no se acredita su manifiesto, devuelve
`media_publication_unknown`, conserva los originales y rechaza repetir esa misma
identidad automáticamente. Un recibo del modelo no sustituye esa comprobación.

## Pruebas añadidas y aceptación pendiente

Los casos Python usan exclusivamente archivos, tonos y fuentes artificiales:

- extracción/citas Unicode, HTML sin fetch, filas y valores Office inertes;
- XML hostil, macros, ZIP bombs, duplicados, rutas y límites;
- identidad de fuente, symlinks/hardlinks, cambio de directorio, hash CAS;
- sesión/workspace exactos, temporales RAM, revocación y persistencia;
- idempotencia, publicación parcial, cuota cruzada RAM/disco y lock ocupado;
- resultados nativos falsificados, frames persistidos, SHA/tipo/estado discordantes;
- codec OPUS/VORBIS real con tono sintético, reducción a mono, duración,
  cancelación y ausencia de fallback externo.

Ruff y AST son controles estáticos, no sustitutos de esas pruebas. Las suites,
paquetes, smoke y compilación se ejecutan en CI remota. El cierre integrado
deberá registrar archivos reales del corpus sintético, hashes/citas coincidentes,
errores/cancelación/revocación, reproducción nativa y resultados de Codex real.
Este documento no declara cumplida esa aceptación antes de disponer de evidencia.
