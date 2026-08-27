# ADR-0122: resultados privados y mutaciones con respuesta determinista

## Contexto

Una inferencia posterior a leer Mail o Calendar añade latencia y puede transmitir metadatos
privados. Además, las mutaciones incorporadas después del primer MVP exigían confirmación en el
broker, pero no todas estaban registradas en el gestor central de aprobaciones.

## Decisión

1. Listados no vacíos de Mail y Calendar se validan y presentan mediante plantillas locales. Se
   muestran como máximo cinco elementos y se cuenta el resto.
2. Errores y contratos inválidos de esas lecturas también producen una respuesta local fija.
3. El gestor admite una sola confirmación exacta para contactos, recordatorios, CoreAudio,
   multimedia y Spotlight, además de las mutaciones existentes.
4. Después de ejecutar, cada payload se valida por herramienta. Acción, título o estado se cotejan
   con los argumentos autorizados cuando el sistema puede verificarlos.
5. Una salida inválida no llega a un sintetizador: el trabajo falla cerrado y la auditoría conserva
   únicamente metadatos del resultado.

## Consecuencias

Correo, agenda, sistema y aplicaciones responden más rápido y con texto estable para voz. Los datos
privados no se exponen a un modelo solo para redactar una confirmación. La conversación libre y la
planificación ambigua conservan el cerebro híbrido; la autorización nunca depende de ese modelo.
