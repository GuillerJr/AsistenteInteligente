# ADR-0032: Stream local y opt-in para activar “Jarvis”

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** reconocimiento Speech continuo sería más pesado y expondría contenido innecesario.
2. **Eliminar:** no se añade SDK, red, archivo de audio, transcript previo ni escucha por defecto.
3. **Simplificar:** AVAudioEngine alimenta directamente un único clasificador SoundAnalysis/Core ML.
4. **Acelerar:** una coincidencia confirmada reutiliza el turno de voz, permisos e IPC existentes.
5. **Automatizar:** el estado opt-in se restaura al arrancar y el stream se pausa ante audio activo.

## Decisión

La escucha solo puede habilitarse desde la Menu Bar cuando el bundle contiene un
`JarvisWakeWord.mlmodelc` válido y TCC permite micrófono. La preferencia se almacena en UserDefaults;
un activo ausente o inválido falla de forma cerrada antes de crear `AVAudioEngine`.

`SNAudioStreamAnalyzer` recibe PCM efímero directamente desde un tap de 4096 frames. No se escribe,
transcribe ni envía audio. La compuerta primero exige dos resultados de fondo para armarse y después
dos resultados consecutivos separados por hasta 1,5 segundos, con `jarvis` como primera
clasificación y confianza mínima de 0,85. Tras activar se desarma, aplica un enfriamiento monotónico
de cinco segundos y rechaza resultados repetidos o no monotónicos. Así un stream que comienza sobre
ruido clasificado constantemente como `jarvis` no puede iniciar turnos en bucle.

El detector llama al mismo `startVoiceTurn()` usado por el menú y el atajo. Se detiene durante la
captura Speech, el enrolamiento y la voz sintetizada, y se restaura únicamente si el usuario mantiene
el opt-in. Un fallo del analizador detiene el stream y se refleja en la Menu Bar.

## Consecuencias

El coste permanente se limita a captura nativa y una inferencia Core ML con CPU/Neural Engine; no
existe runtime adicional. La funcionalidad permanece indisponible hasta entrenar, empaquetar y firmar
un modelo real. La identificación del hablante y la calibración de falsos positivos quedan fuera de
este corte y deberán medirse con datos reales antes de distribución.
