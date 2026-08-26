# ADR-0115: compuerta local-first y contexto temporal

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. Antes de ampliar una API de modelos, cada capacidad se evalúa en este orden: lógica determinista,
   API nativa de macOS, Apple Intelligence on-device y, solo al final, proveedor remoto.
2. Ser local no omite broker, TCC, auditoría o confirmación cuando la capacidad los requiera.
3. Las preguntas exactas de zona horaria responden con el offset UTC del `datetime` local. No se
   inventa una ciudad o identificador IANA que el reloj no aporte de forma fiable.
4. Las preguntas exactas de tiempo activo leen `CLOCK_MONOTONIC_RAW`. En Darwin este reloj continúa
   durante reposo y no se altera por correcciones del reloj civil.
5. El tiempo activo se limita a diez años, se redondea hacia abajo a minutos y usa una frase local.
   Un valor inválido o un fallo del reloj nunca escala a un modelo.
6. Ninguna de estas consultas usa memoria, herramienta, proceso externo, red, permiso o persistencia.

## Motivo

El contexto temporal básico ya existe en el kernel y en Foundation. Enviarlo a un modelo añade
latencia y superficie de datos sin mejorar la respuesta. La compuerta fija además el orden de
desarrollo solicitado: agotar primero capacidades locales verificables.

## Límites

- La zona horaria se expresa como offset actual; no resuelve zonas de otras ciudades.
- El tiempo activo mide desde el origen monotónico de arranque de macOS y no es historial de uso.
- Consultas compuestas o relativas a otro equipo conservan el cerebro normal.
- Las siguientes iteraciones local-first priorizan estado de audio/red y controles nativos acotados
  antes de ampliar endpoints de modelos.
