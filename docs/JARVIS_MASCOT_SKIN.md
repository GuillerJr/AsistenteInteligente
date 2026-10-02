# Mascota original de Jarvis

Jarvis es un pequeño custodio flotante: cuerpo de semilla en microfibra cian,
cresta asimétrica, aletas recogidas, visor de grafito y collar con broche de latón.
La silueta, los accesorios y la cara reemplazan al peluche con monóculo usado
durante la primera exploración visual.

El cuerpo se generó desde cero con la herramienta integrada `image_gen`, sin
imágenes de referencia. Es un PNG RGBA transparente. Los ojos luminosos, la
sonrisa, el indicador del broche y las expresiones de escucha, procesamiento,
respuesta y atención se dibujan con Canvas nativo. El cuerpo flota, respira y
gira suavemente; la cara parpadea y mira. «Reducir movimiento» deja las expresiones
estáticas. No se llama a un modelo de imágenes durante el uso de Jarvis.

Activo: `native/AegisAudio/Sources/AegisMenuBar/Resources/JarvisMascotBody.png`.
El paquete Swift y el instalador incluyen el recurso para funcionamiento offline.
La resolución del Canvas se mantiene fija durante la transición del notch y
la textura se carga una sola vez.

## Prompt final empleado

> Use case: stylized-concept.
> Asset type: original production body texture for the native animated Jarvis mascot in a black macOS camera notch, transparent PNG.
> Primary request: design ONE entirely original, premium friendly floating assistant creature, a tiny technological concierge guardian. NOT a furry circular puff, NOT a flower or cloud, NOT a recognizable existing character. This is a body/blank-visor layer; the app will draw animated eyes afterwards.
> Subject and silhouette: a compact rounded kite / seed-shaped body, broad gently rounded upper shoulders tapering into a plump rounded lower tip; one small swept-back crest on the upper-right silhouette, and two very short soft side fins folded close to the body like a welcoming attendant's arms. The crest and fins must remain inside the complete silhouette and feel integrated, not separate floating objects. The body is charming, calm, capable and compact, with no legs or feet. It must remain recognisable at a 30-pixel display size.
> Materials: turquoise-cyan body in very fine dense short microfiber velvet, tactile and softly sculpted, subtle seam lines joining two broad body panels; narrow dark teal satin edging at the sides. Premium restrained toy-like 3D illustration, not hard metallic armor, not an excessively furry ball.
> Face area: a single smooth broad dark graphite horizontal oval visor with softly rounded sides, completely BLANK, no eyes, pupils, facial features, eye reflections, symbols or mouth inside it. Visor centered horizontally with its center at approximately 44 percent of the square image height, spanning about 46 percent of image width and 18 percent of image height. The dark visor is a physical embedded facial panel and has a very subtle cool rim.
> Accessories: a narrow dark teal concierge collar below the visor, wrapping softly around the body at about 62 percent of image height; its central clasp is a small matte warm brass diamond pin, centered at about 64 percent of image height. No monocle, no glasses, no headset, no hat, no bow tie, no weapons. Keep accessories integrated and simple.
> Lighting: soft studio light from upper left, rich dimensional teal shadows at lower-right, cyan silhouette readable against pure black, restrained highlights, no detached glow or cast shadow outside the character.
> Composition: exactly front-facing orthographic-like view, body upright, no perspective yaw or tilt, one centered complete mascot occupying about 84 percent of the square canvas height and 80 percent of its width, about 8 percent transparent margin, no cropping.
> Background: genuine transparent alpha, no backdrop, floor, checkerboard drawn into pixels or environment.
> No text, letter J, logos, watermark, other characters or ornamental objects. Output the single high-quality original blank-visor mascot body asset only.

## Interacción conectada al asistente

Al acercar el cursor, el notch presenta una conversación compacta. Un clic fija
la mascota; Escape o un clic fuera lo recoge. El doble clic sobre la mascota
inicia una captura de voz o interrumpe una respuesta hablada para escuchar otra
instrucción. «Hablar» expone la misma acción al teclado y a VoiceOver.

El campo de texto usa `sendChatMessage`: el mismo historial, proveedor NVIDIA,
canal de herramientas y broker de permisos que la ventana de Chat. El estado
muestra envío, procesamiento, escucha y respuesta a partir de eventos reales;
no hay porcentajes inventados ni se atribuye a una tarea la telemetría global de
otras sesiones. El texto reconocido se muestra después de finalizar la captura.
La respuesta puede seleccionarse y conserva el renderizador Markdown del chat.

Al arrastrar archivos locales se prepara la cola de adjuntos compartida con Chat:
hasta cuatro archivos, una imagen, documentos de hasta 32 KB de texto y un total
de 64 KB. No se envían a NVIDIA hasta pulsar Enviar. Se mantienen los formatos,
validaciones y límites del cargador existente. Los adjuntos pueden retirarse
individualmente desde la mascota.

La voz utiliza el chat seleccionado únicamente con identidad y presencia del
propietario verificadas. Seleccionar una conversación nueva también inicia una
sesión de voz nueva; una voz no verificada no recibe el historial del chat.
Después de un turno de voz vinculado se actualiza el historial sin suprimir la
respuesta hablada ni sobrescribir una selección o un turno más reciente.

Las preguntas, decisiones y recuperaciones muestran una burbuja contextual con
«Revisar y responder», que abre el diálogo existente. Ningún gesto sobre la
mascota concede autorización. Las respuestas de voz a aclaraciones o selección
de navegador también requieren verificar al propietario.

Los avisos de batería, red, calendario local y rendimiento solo se muestran si
los avisos proactivos ya están habilitados. Usan su control de frecuencia actual,
no despliegan automáticamente el notch y caducan en 30 segundos. Una tarea o
una decisión pendiente tiene prioridad. Al bloquear macOS se limpia la entrada
y el aviso, se oculta el contenido privado y se recoge el panel.

La expansión conserva el reloj de pantalla y la transición interrumpible; el
panel de conversación de 340 × 340 puntos queda debajo del menú y de la cámara.
El medidor de audio actualiza únicamente el dibujo de la mascota, evitando
reprocesar Markdown o el editor por cada muestra.
