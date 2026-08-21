# ADR-0037: Guía determinista para un enrolamiento diverso

Fecha: 2026-08-21

## Filtro del algoritmo

1. **Cuestionar:** 20 repeticiones idénticas pueden memorizar una sola distancia, intensidad o ruido.
2. **Eliminar:** no se añade telemetría, perfil acústico, configuración ni dependencia.
3. **Simplificar:** el conteo ya disponible selecciona una instrucción fija para la siguiente muestra.
4. **Acelerar:** la ventana indica la variación antes de cada clic, sin pasos nuevos.
5. **Automatizar:** la guía avanza únicamente cuando una muestra supera el control de calidad.

## Decisión

`WakeWordEnrollmentGuidance` deriva texto de `label + acceptedCount`. Las muestras positivas rotan
voz habitual, menor intensidad, distancia/dirección y ruido cotidiano. Las negativas rotan silencio,
ruido, conversación sin la palabra de activación y palabras fonéticamente cercanas.

La función no conserva estado. Un conteo negativo se normaliza a cero y, después del mínimo de 20,
solicita variaciones naturales adicionales. La interfaz etiqueta el texto como `Siguiente` para que
la condición corresponda al próximo clip, no al ya aceptado.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** mejora el dataset local del detector de voz.
- **Fase 5 — Voice-first:** reduce falsos positivos sin habilitar escucha ni red durante el enrolamiento.

## Consecuencia

El dataset mínimo cubre condiciones más variadas con cero tráfico y cero metadatos nuevos. La guía
no garantiza precisión: el entrenador conserva su validación y el detector continúa *fail-closed* si
el modelo falta o no es válido.
