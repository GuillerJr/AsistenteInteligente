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

## Presencia con comportamiento contextual

El notch contiene únicamente la mascota y un estado breve. El editor, las
respuestas, los archivos y el historial viven en la ventana de Chat. No hay
menú de opciones ni botones de configuración dentro del notch.

`MascotBehaviorState` decide la actitud a partir de eventos reales, con un reloj
inyectable y reglas deterministas. No elige animaciones al azar ni consulta un
modelo para mover la cara. El cursor se sigue solamente dentro del panel del
notch, con interpolación de mirada y cabeza. No se rastrea el escritorio.

Un clic o una caricia de arrastre provoca un asentimiento y una expresión de
contacto. Las reacciones repetidas se limitan en frecuencia y conservan una
pequeña memoria de contacto solo durante esta sesión. Tras 75 segundos sin
interacción ni trabajo, cierra los ojos para descansar; al regresar el cursor,
saluda una vez y vuelve a prestar atención. El descanso es visual: no modifica
la escucha ambiental ni ninguna preferencia.

La escucha, el trabajo, la respuesta hablada, los errores y las decisiones
pendientes tienen prioridad sobre las caricias. Un gesto de finalización se
activa únicamente al pasar de una tarea real a un resultado confirmado; ni el
inicio de la aplicación, ni la cancelación, ni un fallo simulan éxito.

El doble clic inicia voz o interrumpe una respuesta para escuchar otra
instrucción. Durante la escucha, otro doble clic cancela la captura sin enviar
una solicitud. Si hay una decisión pendiente, abre el diálogo existente y
recoge el notch para dejar visible el modal. Ningún gesto aprueba acciones.
El mismo gesto está disponible mediante la acción de accesibilidad «Hablar con
Jarvis» y el atajo de voz actual.

La voz conserva el contexto de Chat únicamente con identidad y presencia del
propietario verificadas. Los avisos proactivos usan la preferencia y límites ya
existentes, no despliegan el notch automáticamente ni sustituyen una tarea
activa. No se expone su contenido privado en la mascota. Al bloquear macOS se
limpia la memoria de contacto y de mirada, y se recoge el panel.

La mascota mantiene el recurso y el Canvas originales. Su reloj de dibujo no
actualiza las demás ventanas. «Reducir movimiento» elimina la flotación, el
asentimiento y el giro, conservando expresiones comprensibles. El panel abierto
mide 220 × 180 puntos y permanece debajo del menú y de la cámara durante toda
la transición. El ejecutable de interfaz conserva `-Osize` en producción y el
límite de distribución de 24 MiB.
