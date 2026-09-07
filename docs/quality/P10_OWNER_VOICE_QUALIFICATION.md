# P10 — Calificación física de voz del propietario

P10 cierra la última frontera que P7, P8 y P9 dejaron deliberadamente fuera: comprobar con el
micrófono real que Jarvis despierta con la palabra entrenada, reconoce al propietario, conversa,
acepta una continuación sin repetir su nombre y se deja interrumpir de manera orgánica. Una prueba
unitaria o una voz TTS no pueden sustituir esta evidencia.

## Compuerta

```bash
./script/p10_voice_gate.sh
```

La compuerta vuelve a ejecutar P9 y el harness evolutivo antes de abrir el flujo físico. La parte
automática sintetiza 15 voces negativas locales, evalúa el modelo compilado contra esas muestras y
contra el enrolamiento del propietario, y exige separación completa con un umbral entre `0.78` y
`0.95`. No usa Internet ni modifica el modelo si la calibración ya es segura.

Después espera hasta cuatro minutos una secuencia guiada de tres interacciones:

1. decir «Jarvis» y pedir una respuesta suficientemente larga;
2. volver a decir «Jarvis» durante esa respuesta y emitir una orden corta tras la interrupción;
3. durante la ventana posterior de cinco segundos, continuar sin repetir «Jarvis».

## Los cinco contratos

| Contrato | Evidencia exigida |
|---|---|
| `voice.runtime` | Build exacto, daemon íntegro, TCC, wake word, hablante y energía compatibles. |
| `voice.adversarial_calibration` | ≥15 distractores, ≥5 muestras reales y clases separadas. |
| `voice.wake_word_activation` | Al menos una detección física posterior a instalar el build. |
| `voice.conversation_continuity` | ≥2 trabajos vocales, ≥90% propietario, dos reproducciones y un seguimiento. |
| `voice.organic_interruption` | Al menos una cancelación + fade de voz verificada durante la sesión. |

Los contadores viven en `runtime-evidence.json` con modo `0600` y se reinician al cambiar la revisión
Git. El esquema v2 añade únicamente contadores y latencias: no conserva audio, texto reconocido,
respuestas, identificadores biométricos ni frases pronunciadas. El reporte P10 tampoco los expone.

## Estados honestos

- `passed`: cinco de cinco contratos físicos aprobados.
- `needs_interaction`: falta una parte de la secuencia humana; no es reemplazada por un mock.
- `needs_attention`: hubo muestras, pero reconocimiento o latencia quedaron bajo el objetivo.
- `blocked`: build incoherente, permiso ausente, seguridad no íntegra, presión térmica o Low Power
  Mode. La compuerta no cambia preferencias del sistema para aprobarse a sí misma.

## Límite

P10 mide al propietario, el entorno acústico y el hardware de esta instalación. No certifica todas
las habitaciones, micrófonos externos, idiomas o condiciones de ruido posibles. Las recalibraciones
futuras deben conservar el mismo conjunto adversarial y añadir muestras reales autorizadas; nunca
deben rebajar el umbral para obtener una marca verde.
