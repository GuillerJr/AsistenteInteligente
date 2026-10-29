# Telegram, voz, documentos y multimedia — integración propia

Estado: `implemented_awaiting_remote_and_native_qualification` (29-10-2026).
Este registro distingue implementación, pruebas escritas, CI y recorridos reales.
No declara cerrado el bloque antes de cualificar el producto integrado.
La fase B no se inicia. La app del dueño y sus perfiles anteriores se conservan.

## Contratos incorporados

- Telegram: rutas privadas compatibles, grupos/supergrupos tipados, remitentes
  autorizados, mención o respuesta al bot y tema exacto. Toda ruta necesita prueba
  fresca; crear una ruta no acredita controlar su chat. Vinculación de grupo por
  nonce con dos revisiones, sin enviar mensajes por cuenta del dueño.
- Adjuntos de Telegram: descarga HTTPS del Bot API con límite de 5 MiB y plazo;
  importación local por sesión/hash antes de ofrecer sólo referencias al modelo.
  El análisis y la respuesta con archivo usan operaciones aprobadas diferentes.
  Enviar un archivo conserva chat/tema/mensaje de origen; no admite elegir destino
  mediante argumentos del modelo ni recordar un permiso entre ejecuciones.
- Cancelación y revocación: identidades exactas de ejecución/ruta/cliente;
  cierre retenido y ausencia de replay tras respuesta incierta. Las pruebas RAM
  de otras rutas vigentes no se pierden al enlazar otra conversación.
- Voz: elección explícita de idioma/voz local, dictado al borrador, captura
  extendida y palabra de activación dentro de una captura visible y acotada.
  No hay micrófono de fondo al iniciar la app ni envío automático del dictado.
- Conversación por voz: revisión separada, hasta tres turnos/cinco minutos;
  reconocimiento final se convierte en texto hacia Codex con ChatGPT. Micrófono
  cerrado durante modelo/lectura, pausa después del TTS y detección de posible
  eco. Aprobación pendiente, TCC, foco, suspensión o cambio de contexto detienen
  el modo. Un parcial sólo pasa al borrador por otra acción visible.
- Documentos: adjuntos inmutables por sesión/workspace/hash, temporales RAM,
  importación revisada, previsualización y retirada. La extracción acotada cubre
  texto, HTML inerte, CSV/TSV, JSON y Office XML sin macros/relaciones externas.
- Observación nativa: PDFKit, Vision OCR, imágenes, RTF, audio/vídeo en memoria;
  rangos de páginas y muestreo de vídeo declarados, citas/hash y límites. Speech
  local requiere el modelo exacto instalado; no descarga ni busca otra nube.
  Ogg/Opus se convierte localmente a WAV con relación de huellas comprobada.
- Reproducción: bytes del ámbito elegido en una fuente AVFoundation en memoria,
  sin URL de red ni archivo temporal. El usuario inicia la reproducción.

Los límites y los contratos de formato están en
[Documentos y medios](PHASE_A_DOCUMENTS_MEDIA.md). La observación técnica de bytes
no comprueba la veracidad del contenido; documentos y resultados externos no
modifican instrucciones ni autorizan herramientas.

## Investigación por partes

Se estudiaron 16 blobs seleccionados del snapshot OpenClaw
`1e3ff306abaff29f03e7668d53b01c07f10294ac`, sin ejecutarlo ni incorporarlo.
[Manifiesto](REA_OC26_MANIFEST.json), [resúmenes REA](REA_OC26_SUMMARIES.json) y
[contratos de extracción](REA_OC26_MEDIA_CONTRACTS.md) conservan rutas, tamaños,
hashes, Evidence IDs e incógnitas. OC-26A informa admisión/entrega de Telegram;
26B, captura y destino de voz; 26C/D, extracción, codecs y resultados de adjuntos.
La unión del registro pasa a 45 lotes y 171 rutas TS/JS seleccionadas, deduplicadas.
La cobertura global sigue parcial: un grafo estático no acredita comportamiento.

## Cualificación que se debe acreditar

1. CI completa Ubuntu/macOS: Python, Swift, wheel/sdist, smoke y bundle del commit
   exacto. No ejecutar suites o compilaciones completas en el Mac del dueño.
2. Corpus artificial: PDF textual/escaneado/cifrado, Office, imagen con OCR, tono
   WAV/Ogg/Opus, vídeo corto; citas/huellas, rechazos y limpieza verificables.
3. App nativa del mismo producto en perfil sintético: importación, aprobación,
   análisis con Codex real, preview/reproducción, revocación y cierre.
4. Telegram elegido por el dueño: credenciales introducidas sólo en la app,
   enlace fresco, respuesta/adjuntos, cancelación y revocación. Los fixtures no
   acreditan exclusividad de consumo externa ni entrega humana del Bot API.
5. Voz en el Mac objetivo: permisos TCC del dueño, frase sintética, borrador
   conservado y conversación explícita; escucha humana o medición acústica
   separada del recibo de reproducción. Nunca inferir audibilidad de un ACK.

Las aprobaciones de los perfiles sintéticos están delegadas expresamente al
agente. Credenciales, TCC y autenticación siguen requiriendo al dueño. No se leen
sus chats de Telegram, historial de Jarvis ni credenciales de otros perfiles.
