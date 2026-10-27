# Encoder local elegido — preparación del 27-10-2026

El dueño eligió MiniLM local CPU. Codex con ChatGPT conserva la conversación.
No se conectan NVIDIA, otro proveedor, modelos alternativos ni respaldos ocultos.
El código de esta carpeta es propio; no importa implementación de OpenClaw.

Referencia oficial: [modelo y licencia declarada Apache-2.0](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2),
[exportaciones ONNX](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2/tree/main/onnx)
y [metadatos con tamaños/hashes](https://huggingface.co/api/models/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2?blobs=true).
Revisión fija `e8f8c211226b894fcb81acc59f3b34ba3efd5f42`.

- Modelo: 118.412.398 bytes; SHA256 `783fea82d71a58179b830a4dbd2d58447e640609e98eedf9ffa12622d375a672`.
- Tokenizer: 9.081.518 bytes; SHA256 `2c3387be76557bd40970cec13153b3bbf80407865484b209e655e5e4729076b8`.
- README: 3.888 bytes; SHA256 `1e98ea05b0de579fcaad3d625b62ea55647142ed674d5f5ebf1440e4bbbb6f23`.
- [Texto Apache-2.0 oficial](https://www.apache.org/licenses/LICENSE-2.0.txt): 11.358 bytes;
  SHA256 `cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30`.

Adquisición autorizada verificada y publicada exclusivamente en
`/private/tmp/jarvis-minilm-approved.9md4bz6h/artifacts`: 127.509.162 bytes de artefactos,
más manifiesto. No es una carpeta del dueño ni una instalación del producto.
El CDN devolvió ETags distintos del SHA256 LFS: se comprobaron los bytes, no el ETag.

Dependencias propuestas: `onnxruntime==1.31.1`, `numpy==2.3.5`, `tokenizers==0.22.1`.
Los wheels oficiales admiten CPython 3.11 en macOS 14 ARM64 y Linux glibc 2.28.
No se instalaron durante esta preparación. [ORT](https://pypi.org/project/onnxruntime/1.31.1/#files),
[NumPy](https://pypi.org/project/numpy/2.3.5/#files),
[tokenizers](https://pypi.org/project/tokenizers/0.22.1/#files).

Receta propia: CPU exclusivo, un hilo, 384 dimensiones, mean pooling con máscara
y normalización L2. Fragmentos de documentos cubren todos los bytes UTF-8, cada uno
con máximo 768 bytes y 128 tokens incluyendo especiales; solapamiento de 16 tokens.
Una consulta admite máximo 512 bytes y 128 tokens enteros o falla explícitamente.
El límite de 128 procede de sentence_bert_config, aunque otros metadatos dicen 512.
No se altera manualmente el casing: manda el tokenizer fijado.

El worker carga sólo bytes recién verificados, no rutas que puedan sustituirse
entre hash y decoder. En macOS utiliza Seatbelt sin escrituras ni red; cuota de
archivo cero y core dumps desactivados. Linux CI no acredita una sandbox equivalente.
Cancelar espera cierre/reap y retirada del temporal; un cierre incierto conserva
el bloqueo. Las citas prueban bytes del snapshot, no verdad de una fuente externa.

El límite de CPU de 60 segundos pertenece a toda la vida del worker. Para admitir
corpus grandes se retira cooperativamente entre peticiones al acumular 25 segundos
de CPU: el padre confirma su cierre antes de devolver el resultado ya calculado.
La siguiente petición inicia el mismo encoder sin reenviar la anterior. Cada
petición conserva un plazo máximo de 30 segundos, además del plazo del trabajo.

Una caché dañada se prepara en otra generación mediante una acción explícita;
no se modifica, mueve ni borra el original. El control del host conserva dos
copias como máximo y activa la nueva por CAS después de cerrar el worker. Retirar
la copia inactiva exige otra decisión del dueño, identidad física registrada y
layout exclusivo del modelo. Los archivos desconocidos se conservan. Un manifiesto
dañado sólo se admite para retiro si existe el registro propio de preparación
verificada; un `stat` reciente no constituye esa autoridad. Interrupciones después
de publicar o retirar archivos conservan recibos de estado indeterminado.

Pasaron Ruff, AST y tres stubs propios diminutos en RAM para UTF-8, espacios y
límites. No se ejecutó inferencia ni se acredita calidad semántica todavía.
La prueba real usa `JARVIS_MINILM_TEST_ASSETS` y
`JARVIS_MINILM_REQUIRE_REAL=1`; si faltan artefactos en esa cualificación, falla.
Exige paráfrasis españolas, cobertura más allá del token 128 y cierre real.

Para la interfaz: mostrar modelo, CPU, licencia, tamaño y estado verificable;
`missing_model`, `invalid_model`, `unavailable_runtime`, `capacity`, `timeout`,
`cancelled` y `cleanup_pending` se traducen al español. No presentar un skip,
fixture o índice sólo léxico como acreditación del motor semántico real.
