# ADR-0013: Respuesta hablada local y acotada

- Estado: reemplazado por ADR-0070
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** un proveedor TTS remoto no es necesario para cerrar el primer bucle voice-first.
2. **Eliminar:** no se añaden streaming, caché, selector de voz, Markdown visual ni historial.
3. **Simplificar:** `jobs.status` autenticado y `AVSpeechSynthesizer` nativo.
4. **Acelerar:** polling fijo de 500 ms con un límite total de 60 segundos.
5. **Automatizar:** la respuesta terminada se pronuncia sin abrir una ventana y una nueva captura
   interrumpe la locución anterior.

## Decisión

El cliente Swift valida el UUID, los cinco estados permitidos, la coherencia de campos terminales y
el límite JSON de 24 KiB del resultado. Solo un job `completed` puede llegar al sintetizador.

La locución usa la voz local `es-US`, normaliza espacios y se limita a 2.000 caracteres. El texto
vive únicamente en memoria durante el turno: no se muestra, registra ni persiste. Fallo, cancelación,
respuesta inválida o timeout cierran el turno sin revelar detalles internos.

## Encaje en el roadmap

- **Fase 3:** completa entrada y salida de voz usando APIs locales de macOS.
- **Fase 5:** entrega el primer ciclo voice-first sin chat, ventana ni HUD prematuro.

## Consecuencia

El enjambre ya puede responder por voz con cero dependencias TTS externas. Voces configurables,
streaming y wake word permanecen fuera del MVP.
