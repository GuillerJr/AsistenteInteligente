# Telegram, voz, documentos y multimedia — integración propia

Estado: `in_progress` (29-10-2026).
Este registro distingue implementación, pruebas escritas, CI y recorridos reales.
No declara cerrado el bloque antes de cualificar el producto integrado.
La fase B no se inicia. La app del dueño y sus perfiles anteriores se conservan.

La [CI completa de `dbad498`](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40936051756)
aprobó Python (3448 Ubuntu y 3494 macOS), Swift (605 casos, uno omitido y cero
fallos; 12 contratos del lanzador), paquetes, smoke, bundle y limpieza. Las
suites y compilaciones se ejecutaron en los runners remotos. El
[registro](PHASE_A_TELEGRAM_VOICE_MEDIA_QUALIFICATION.json) conserva también los
intentos fallidos y las identidades de cada recorrido real.

La revisión funcional posterior `628ab0e` corrige el monitor de cambios de
entrada. Su [CI](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40941195038)
aprobó Ubuntu y compiló el bundle/helper, pero Swift tuvo un fallo entre 614
casos (uno omitido). Los 13 casos del monitor pasaron; falló el oráculo asíncrono
de cancelación del navegador. Se preparó una corrección sólo de esa prueba.
MacPython, paquetes y smoke posteriores no se ejecutaron: todavía faltan la CI
completa del nuevo commit y la repetición real de voz con su bundle.

Telegram acredita privado y grupo separados, documento con marcador/fuente,
envío recibido por el dueño, rechazo sin subida, retirada con original intacto
y reactivación sin ejecutar los mensajes de la pausa. El corpus mixto con Codex
real acredita contenido y fuentes de DOCX/CSV, imagen y vídeo parcial después de
corregir la entrega conjunta de texto e imágenes aprobados. WAV/Ogg se
transcribieron localmente; el dueño confirmó una lectura audible y dictado sin
envío automático.

Los ocho formatos adicionales acreditaron una inspección por archivo, recibos
conocidos, originales y copias CAS con huellas coincidentes y marcadores/citas
recuperados por Codex: XLSX, PPTX, RTF, HTML inerte, JSON, XML, TSV y PDF escaneado.
En el PDF con tipografía segmentada, el OCR produjo `TARLIS Y2` y la lectura
visual `JARVIS 42`; esa discrepancia sigue registrada. Otro PDF sin capa de
texto, con letra regular, recuperó desde el recibo OCR literal
`MARCADOROCRZAFIRO3582` y `VALOR 76`, con cita `page:1`. No se afirma exactitud
universal del OCR ni se sustituye el fallo anterior por este resultado.

La app rechazó una inspección aprobada de PDF cifrado con
`media_native_pdf_encrypted`, sin repetirla ni probar contraseñas. El archivo de
16 MiB más un byte se rechazó antes de presentar revisión/importación; su nombre
y SHA no aparecen en el catálogo y el original conserva su huella. La solicitud
exacta de `media_inspect` sobre el adjunto retirado también quedó bloqueada:
Permitir/Recordar deshabilitados, rechazo nativo y cero recibos de ejecución,
claims o CAS nuevos. Este caso acredita el gate previo al despacho; no inventa
una respuesta «revoked» del ejecutor que nunca se llegó a llamar.

El intento inicial de probar la revisión 201a5b7 falló porque su lanzador de QA
declaraba un script como ejecutable principal y la solicitud de Speech abortó
por TCC. El informe del proceso propio acredita ausencia de bundle ID y
atribución a otro proceso; no acredita un recorrido de voz satisfactorio. El
paquete original de CI sí declara el Mach-O principal. Con `dbad498`, el helper
de CI inició la app mediante NSWorkspace/LaunchServices, sin wrapper ni Popen de
la interfaz. El dueño concedió Speech desde la app y el aborto anterior no se
repitió. Los dos intentos de micrófono posteriores se detuvieron antes del
estímulo con el aviso de cambio de entrada; conservaron el borrador y no enviaron
un run. La fuente trataba cualquier notificación de configuración como cambio
de entrada, sin comprobar su identidad. La causa concreta de hardware de esos
intentos no quedó establecida; faltan la validación completa de la corrección y
su repetición real. Los procesos del harness fallido se cerraron
de forma explícita y los originales se conservaron. Ver el
[contrato de arranque QA](NATIVE_QA_LAUNCH.md).

El reproductor real acreditó pausa, búsqueda mientras estaba pausado, selección
de auriculares conservando posición, reanudación explícita y audio confirmado por
el dueño. La primera retirada física reportada no era una desconexión de
CoreAudio y se conserva como intento no cualificado. Tras desconectar realmente
esa salida, la UI la retiró, detuvo el reproductor y no pasó al altavoz. Cerrar y
abrir de nuevo volvió a posición cero, pausado y sin reutilizar la selección
anterior. El preview Ogg distinguió original y WAV derivado en RAM y avanzó tras
reproducirlo; ese clip no tiene confirmación acústica independiente. No se midió
la salida global antes de las primeras selecciones ni se infiere el instante
interno de liberación de memoria desde la UI.

Quedan la voz real recibida por Telegram tras reconectar el bot, wake/Talk con
el monitor corregido y CI completa, y los recibos finales de cierre/custodia.
Los permisos concedidos y los WAV preparados no acreditan esos recorridos.

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
  sin URL de red ni archivo temporal. El usuario inicia la reproducción. Cada
  reproductor fija una salida física local admitida; no cambia la salida global
  del Mac, busca dispositivos en la red ni usa una salida virtual como respaldo.
  La retirada/cambio de contexto o salida cierra el reproductor sin reanudarlo.
  Los bytes y la huella del original se distinguen de los del WAV derivado.

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
