# ADR-0153: captura visual exclusiva de la aplicación

- Estado: aceptado
- Fases: 3, 4 y 5

## Evidencia

El filtro ScreenCaptureKit ya incluía solo el proceso autorizado y ocultaba el cursor, por lo que el
retículo de Jarvis no aparecía en el JPEG. Sin embargo, `includeMenuBar` seguía habilitado. El reloj y
otros estados del sistema podían cambiar entre dos capturas, aparentar progreso y exponer contexto
ajeno a la aplicación.

## Decisión

1. La política nativa de control visual fija `includeMenuBar`, `showCursor` y `captureAudio` en falso.
2. El helper conserva el filtro positivo que incluye únicamente la aplicación cuyo bundle ID fue
   aprobado; no captura escritorio ni otras aplicaciones.
3. Las tres exclusiones viven en `AegisAudioCore`, se prueban como contrato y se aplican al construir
   `SCContentFilter` y `SCStreamConfiguration`.
4. No se añade recorte, posprocesamiento, dependencia ni segunda captura.

## Consecuencia

La visión remota y la prueba local de progreso reciben solo píxeles atribuibles a la aplicación
autorizada. Jarvis pierde navegación visual genérica por la barra de menús; una futura acción de menú
deberá usar Accessibility y un contrato dedicado en vez de ampliar la captura.
