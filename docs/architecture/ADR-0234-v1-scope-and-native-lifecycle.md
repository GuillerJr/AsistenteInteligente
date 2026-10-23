# ADR-0234: alcance NVIDIA de la 1.0 y ciclo de vida de la interfaz

Fecha: 2026-10-21. Estado: aceptado por el encargo de continuar `V1_PENDIENTES.md`.

La política de inferencia queda sustituida por [ADR-0235](ADR-0235-configured-inference-and-codex-chatgpt.md)
por elección expresa del dueño el 23-10-2026. Las demás decisiones se conservan.

## Contexto

El manifiesto de distribución prometía conversación sin proveedores y un cerebro híbrido,
aunque el daemon usa NVIDIA exclusivamente. Además, decisiones que terminaban antes de
registrarse su ventana podían dejar una escena vacía, y los selectores nativos retenían el
seguimiento del menú al abandonar Ajustes.

## Decisión

- El alcance declara NVIDIA obligatorio, sin respaldo local. El validador rechaza proveedores
  alternativos y capacidades de inferencia que afirmen funcionar sin red. El gate RC3–RC8 usa
  la política `nvidia_only`. Los comandos deterministas siguen siendo locales.
- El manifiesto incluye proyectos, planes, tareas programadas, complementos y navegador.
  Mantiene explícita la certificación física de voz pendiente; no acredita una publicación.
- Cerrar decisiones cancela también el enfoque diferido. La vista comprueba su estado inicial
  y la barra de menús observa la desaparición de la interacción aunque la ventana esté oculta.
- Al navegar por Ajustes o desmontar un selector se cancela el seguimiento de sus menús.
- Abrir únicamente una URL pública explícita usa `browser_open_url` aunque se ofrezca CDP.
  Una referencia a una fuente reciente conserva `LOCAL_CONTEXT_REFERENCE` y la confirmación
  del broker. No se extraen enlaces arbitrarios del historial. Las peticiones compuestas y
  las sesiones de navegador activas mantienen sus controles de destino y observación.
- Los chats nuevos limpian marcadores de planes y seleccionan proyecto en una sola operación
  protegida contra cambios durante un envío. Las fechas conservan sus fracciones de segundo.
- El bundle declara español, incluye el manual local y explica el acceso a la red local.
  Añadir la descripción no concede permisos TCC.

## Validación y límites

Pruebas Python del contrato de alcance, enrutado y broker; XCTest de ventanas, selectores,
planes, fechas, proyectos y chats temporales; suite completa y empaquetado del pre-push.
Las pruebas de interfaz usan datos sintéticos, sin historial personal ni aprobaciones del dueño.

La notarización, la actualización publicada y la validación TCC en una cuenta limpia siguen
siendo puertas separadas. El experimento de declarar dinámico el producto del núcleo no cambió
el enlace de los targets internos y se retiró: P1-14 sigue pendiente, sin relajar la validación
de bibliotecas de los clientes IPC firmados.
