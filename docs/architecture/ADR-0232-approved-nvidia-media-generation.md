# ADR-0232 — Generación aprobada con NVIDIA Cosmos3 Nano

Estado: implementación integrada; generación real pendiente por HTTP 404 del proveedor.

## Decisión

Jarvis ofrece `cosmos_generate_media` al planificador para solicitudes explícitas de
generación. Usa el endpoint alojado documentado por NVIDIA:
`POST https://ai.api.nvidia.com/v1/cosmos/nvidia/cosmos3-nano`, con la clave NVIDIA
existente del llavero. Cosmos se mantiene separado de los modelos de conversación.
No introduce inferencia local ni nuevas credenciales.

Se implementan texto a imagen JPEG, texto a video MP4 y animación de la imagen
adjunta al turno actual. Los prompts y parámetros pasan por el broker con aprobación
de un solo uso. La imagen real se vincula antes de calcular el digest; el modelo
no puede proporcionar rutas, URLs ni sustituir la imagen autorizada. La vista de
aprobación muestra descripción, formato, resolución, duración y presencia de imagen.

Cada generación envía un único POST. Un HTTP 202 puede recuperarse mediante un
identificador UUID en el host fijo de NVCF, sin reenviar la generación. El plazo es
210 segundos; la interfaz espera hasta 240 segundos tras aprobar. Cancelar o perder
la conexión no demuestra que NVIDIA haya detenido el trabajo. Un resultado incierto
se informa sin reintentar automáticamente. Las redirecciones no se siguen y nunca
se transmite la credencial a una URL de descarga suministrada en la respuesta.

## Archivos y canales

El backend verifica el JPEG o la estructura del contenedor MP4 antes de publicar
el archivo. Conserva los bytes originales y su SHA-256: no altera las marcas de
procedencia de NVIDIA. El almacén local tiene permisos privados, rechaza enlaces,
archivos manipulados y rutas arbitrarias, y admite hasta 64 originales o
512.000.000 bytes. El límite por archivo es 48.000.000 bytes; JPEG, 8.000.000 bytes
y cuatro millones de píxeles. Al alcanzar la capacidad rechaza nuevas publicaciones;
no borra archivos automáticamente.

El historial conserva enlaces con tokens opacos. Chat recupera metadatos mediante
IPC autenticado, verifica el original, presenta una miniatura o reproductor y permite
guardarlo mediante el diálogo nativo. Telegram entrega el original como documento,
con referencia a la misma conversación. Su outbox cifrado conserva el avance y
recupera entregas sin volver a ejecutar Cosmos. Un envío de resultado incierto puede
duplicarse al recuperarlo; Jarvis lo indica. Eliminar la conversación impide continuar
su entrega pendiente.

## Alcance y límites

- Resoluciones predefinidas 480/720, proporciones horizontal, cuadrada y vertical.
- Videos de 25 a 197 fotogramas, a 24 FPS; por defecto 49, aproximadamente dos segundos.
- La imagen adjunta utiliza el límite de entrada existente de Jarvis. No se anima
  automáticamente una imagen histórica sin adjuntarla nuevamente.
- La salida es JPEG o MP4; no se promete PNG, GIF, audio ni otros formatos.
- VP9 en MP4 puede necesitar otro reproductor; siempre puede guardarse el original.
- No incluye video a video, controles espaciales ni predicción de acciones físicas.
- Los flujos que devuelvan descargas mediante HTTP 302 no están admitidos.

## Evidencia

El 6 de octubre de 2026 se verificó la ruta en la
[ficha oficial](https://build.nvidia.com/nvidia/cosmos3-nano/modelcard) y la presencia
del modelo en el filtro Free Endpoint. La prueba sintética con la clave configurada
recibió HTTP 404. No produjo imágenes ni videos; no certifica disponibilidad real.
La evidencia privada está en `/private/tmp/jarvis-cosmos-endpoint-diagnostic/report.json`.
Las pruebas con transporte simulado verifican la integración, no la calidad del modelo
ni el acceso efectivo a la API. P10/P11 y la validación física de voz permanecen aplazados.
