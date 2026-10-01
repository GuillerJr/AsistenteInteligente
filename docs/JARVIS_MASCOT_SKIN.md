# Skin de la mascota del notch

La textura se creó con la herramienta integrada `image_gen`, usando la imagen
de peluche con monóculo proporcionada por el usuario como referencia de estilo.
El cuerpo es un PNG RGBA transparente; la cara y el monóculo se renderizan y
animan en Canvas. No se llama a un modelo de imágenes durante el uso de Jarvis.

Activo: `native/AegisAudio/Sources/AegisMenuBar/Resources/JarvisMascotBody.png`.
El paquete Swift y el instalador incluyen el recurso para funcionamiento offline.

## Prompt empleado

Use case: style-transfer. Asset type: transparent body texture layer for a native animated macOS Jarvis mascot. Edit this reference into ONE furry plush circular scalloped puff body, preserving the tactile very fine fur, rounded cloud-like silhouette, front view, soft three-dimensional volume and charming toy quality of the reference. Change orange fur to Jarvis turquoise/cyan fur: medium cyan body with richer teal shaded lower edge and softly lit pale cyan upper-left. Remove BOTH eyes and the monocle completely: absolutely NO face, no eyes, no glasses, no mouth, no other facial marks, because the app will render animated eyes and monocle as separate layers. Keep just the plush body. Genuine transparent alpha background; no floor, no background, no cast shadow outside the body, no text or logo. Center the complete body in a square composition with about 8 percent clear margin on all sides, all fur tips intact. High quality soft studio lighting from upper left, clear readable texture even scaled down. Do not include the original character alongside the edited one. Output only one complete face-free plush cyan body texture.
