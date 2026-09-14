# Pulido 4 — Conversación

Base: `577892b`. Fecha: 2026-09-14.

## Cambios verificados

- Continuidad hasta la respuesta final: la síntesis local conserva el intercambio reciente,
  incluida la corrección del usuario y la pregunta final del asistente cuando se recorta contexto.
  Mantiene presupuestos de voz de 1536 bytes de historial y 512 de contexto relacional.
- Política compartida: corrección actual prioritaria, no inventar contexto ausente, no repetir una
  pregunta contestada y como máximo una pregunta útil. Se respeta el presupuesto de cada modo sin
  aplicarlo al prompt técnico del CLI NVIDIA.
- Intención: `Hola Jarvis, abre Calendar` sigue siendo tarea; saludos solos son conversación.
  Citas/código no activan apoyo o reparación, y una negación conserva su alcance de cláusula.
  `Me refería al segundo informe` y `Quise decir mañana` se reconocen como correcciones.
- Reparación privada: un invitado no recibe ni consume la ventana del propietario. Sigue siendo
  efímera, de un turno, con caducidad de dos minutos y sin nuevos textos persistentes.
- Reinicio explícito: preguntas o comandos entre comillas no cambian sesión; la orden directa
  admite puntuación natural. No se borra memoria ni historial anterior.
- Evaluación: respuesta vacía/invisible no obtiene 100 puntos; se señala exceso de preguntas.
  URLs, versiones, decimales, código y abreviaturas habituales no inflan frases. Referencias
  diferentes no se confunden con repeticiones. `No, soy humano` no se trata como una negación.
  Las nuevas banderas caben en el contrato IPC/SQLite, también al coincidir siete en una muestra.

La suite nueva reprodujo 31 fallos Python antes de corregir el código; la prueba nativa de reinicio
reprodujo seis rotaciones incorrectas. Las guías SwiftPM, Build/Run/Debug y Test Triage de macOS
guiaron la comprobación focalizada y la validación nativa mediante los scripts existentes.

## Reproducción

```bash
.venv/bin/python -m pytest tests/test_dialogue.py tests/test_conversation_quality.py \
  tests/test_graph.py tests/test_jobs.py tests/test_conversation_window.py \
  tests/test_conversations.py tests/test_evaluation_store.py
./script/test_native.sh
.venv/bin/ruff check src tests
```

Las pruebas usan modelos falsos, datos sintéticos y bases temporales. La matriz de síntesis combina
texto/voz, propietario/invitado y disponibilidad/fallo del modelo local. Comprueba que el contenido
privado no llega a NVIDIA y que el fallo local mantiene su respuesta determinista de privacidad.
También comprueba que estilo y temas ya recuperados llegan a la síntesis, pero no al especialista
remoto. Los flujos existentes siguen cubriendo cancelación, aislamiento y expiración de sesiones,
interrupciones, finalización de streams y orden de turnos.

## Alcance

No se modificaron credenciales, modelos elegidos, permisos TCC, opt-in de voz ni memoria real.
El CLI conserva NVIDIA exclusivamente; este trabajo no descarga modelos. No añade polling,
temporizadores, llamadas generativas ni reintentos.

La calidad es una métrica posterior a la respuesta, no un filtro que bloquee contenido ni una
garantía de veracidad. Las reglas lingüísticas son deliberadamente acotadas. Esta revisión no
certifica conversación acústica en vivo, latencia del proveedor ni respuestas reales de un LLM.
