# ADR-0031: Enrolamiento explícito y privado del wake word

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** un entrenador sin una vía segura para obtener voz real no completa el flujo.
2. **Eliminar:** no se añade nube, base de datos, conversión de audio, wizard ni grabación continua.
3. **Simplificar:** dos botones guardan CAF nativo de dos segundos en las clases requeridas.
4. **Acelerar:** 20 muestras por clase producen directamente la estructura que acepta el entrenador.
5. **Automatizar:** progreso, límites y permisos del almacenamiento se verifican en cada operación.

## Decisión

La Menu Bar expone una sola ventana bajo demanda. Cada muestra requiere un clic explícito y permiso
TCC de micrófono ya concedido. `AVAudioEngine` escribe un CAF de dos segundos en un archivo temporal;
solo una captura válida de al menos 0,4 segundos se mueve a `jarvis/` o `background/`.

El dataset vive fuera del repositorio en
`~/Library/Application Support/Aegis/WakeWordEnrollment`. La raíz y las carpetas de clase deben ser
directorios reales con permisos privados; no se siguen enlaces. Cada clase acepta hasta 100 CAF de
5 MiB como máximo y la interfaz señala como mínimo listo 20 por clase. Los archivos confirmados usan
`0600`; los directorios usan `0700`.

El script de entrenamiento adopta esa ubicación cuando no recibe argumentos. La captura y el
entrenamiento siguen siendo acciones separadas: completar el dataset no ejecuta Create ML, no
instala un modelo y no habilita escucha permanente.

## Consecuencias

Jarvis puede recopilar ejemplos reales sin introducir dependencias ni tráfico de red. Las muestras
persisten deliberadamente para entrenamiento local y solo se crean mediante interacción visible.
El usuario conserva la responsabilidad de eliminarlas; no existe borrado implícito ni retención
oculta. El detector en segundo plano continúa pendiente hasta integrar el stream y consentimiento.
