# Mascota original de Jarvis

La skin activa es un dot redondo de terciopelo verde esmeralda, con dos cuernos
cortos de latón curvo, una diadema sencilla y pliegues de capa integrados en su
silueta. Se inspira en la paleta y el carácter de Loki sin reproducir su rostro,
traje o casco. Jarvis conserva una identidad propia de pequeño guardián travieso.

El cuerpo se generó desde cero con la herramienta integrada `image_gen`, usando
la referencia del usuario como guía visual descrita en el prompt. El PNG RGBA
mantiene transparencia real y margen alrededor de los cuernos. La cara queda
vacía en el activo: Canvas dibuja ojos negros brillantes, reflejos cálidos,
parpadeo, mirada y una sonrisa levemente asimétrica. Las expresiones de escucha,
procesamiento, voz y atención siguen el estado real de Jarvis. El indicador del
broche conserva su animación, adaptada al nuevo dibujo.

Activo: `native/AegisAudio/Sources/AegisMenuBar/Resources/JarvisTricksterDot.png`.
El paquete Swift incluye esta skin; `JarvisMascotBody.png` conserva el diseño
anterior como fuente histórica y ya no se empaqueta. La textura se carga una
sola vez. La generación de imágenes no se invoca durante el uso de Jarvis.
El movimiento autónomo, los gestos, los límites espaciales y la prioridad de
las tareas siguen el motor existente, descrito en ADR-0231.

## Prompt final empleado (herramienta integrada)

> Use case: stylized-concept. Asset type: production transparent body texture for Jarvis, an animated macOS notch dot companion. Create one original adorable emerald forest-magic DOT mascot loosely inspired by Loki's green-and-gold palette and horned silhouette, distinctly its own character, NOT a miniature human, not the actor, not a superhero costume replica. A single round, squashy emerald velvet/premium plush orb; dot-like nearly spherical silhouette, no legs, no arms, no robot torso. A tiny restrained gold circlet integrates into the top of the sphere, with TWO compact thick gently curling golden horns, short and rounded, both fully within the square. Suggest a soft dark green cloak by only two subtle folds at the lower outer edges of the round orb, not a long cape. A small gold diamond clasp sits at the lower center of the orb. Rich emerald green and warm satin brass, friendly enchanted guardian, highly polished stylized 3D plush rendering, tactile fine short velvet fur, clean readable silhouettes, soft upper-left studio lighting and very subtle luminous emerald rim on the plush itself. FRONT VIEW, perfectly centered, square composition, one isolated character occupying about 80 percent of the canvas, at least 8 percent transparent padding all around; full horns visible, no ground, no background, no shadow below, real transparent alpha. Important production constraint: the animated eyes and mouth are drawn by code separately. Provide a COMPLETELY EMPTY dark forest-green horizontal rounded oval face patch/window inset in the sphere, centered at x=50%, y=50% of the entire square, roughly 46% canvas width and 23% canvas height. No eyes, no pupils, no brows, no mouth, no symbols anywhere in this empty face patch. Keep the face patch smooth and unobstructed. Orb center slightly below center so horns fit. Gold diamond clasp at x=50%, y=76% canvas. No text, no logo, no watermark, no magic rays outside the body, no particles, no weapon or staff, no duplicates. Must remain legible at 36px; favor bold restrained shapes over ornate engraved details.

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

La mascota usa el nuevo recurso esmeralda y conserva el Canvas nativo. Su reloj de dibujo no
actualiza las demás ventanas. «Reducir movimiento» elimina la flotación, el
asentimiento y el giro, conservando expresiones comprensibles. El panel abierto
mide hasta 280 × 208 puntos y permanece debajo del menú y de la cámara durante toda
la transición. El ejecutable de interfaz conserva `-Osize` en producción y el
límite de distribución de 24 MiB.
