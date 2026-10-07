# ADR-0232 — Generación de imágenes y videos retirada

Estado: retirada el 7 de octubre de 2026 por decisión del usuario.

Se elimina la integración de Cosmos3 Nano: cliente del proveedor, herramientas del
planificador, aprobaciones, controles de Configuración, publicación de imágenes y
videos generados, y su entrega por Telegram. Jarvis no ofrece generación visual.

Se conservan las capacidades previas de conversación, análisis de imágenes y archivos
adjuntos, voz y Telegram. La clave NVIDIA continúa perteneciendo a los modelos de
conversación; esta retirada no cambia credenciales ni elimina conversaciones o
archivos del usuario.

El lector del estado cifrado de Telegram acepta los dos campos de entrega que
introdujo la integración retirada y los descarta al volver a guardar. Conserva el
texto pendiente, los identificadores de mensajes, el progreso de entrega y las
referencias de conversación. Los demás campos desconocidos siguen rechazándose.

La implementación previa y la investigación de disponibilidad permanecen en el
historial Git. P10/P11 y la validación física de voz continúan aplazados.
