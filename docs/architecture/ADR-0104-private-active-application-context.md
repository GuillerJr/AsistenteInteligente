# ADR-0104: contexto privado de la aplicación activa

- Estado: aceptado
- Fases: 3 y 5

## Decisión

1. Una gramática cerrada reconoce preguntas exactas en español o inglés sobre la aplicación activa
   después de la transcripción final on-device y antes de enviar el transcript mediante
   `voice.submit`. El preflight normal del turno permanece intacto.
2. `Jarvis.app` consulta exclusivamente `NSWorkspace.shared.frontmostApplication?.localizedName`.
   No obtiene bundle identifier, ventanas, documentos, pantalla o contenido.
3. El nombre se rechaza si está vacío, contiene controles o supera 160 bytes UTF-8; los espacios se
   normalizan antes de pronunciarlo. No se registra el nombre ni se persiste la respuesta.
4. La locución reutiliza la salida de voz y el aislamiento del wake word existentes. No se añade
   proceso, dependencia, permiso, red, modelo, memoria, método IPC o sondeo.
5. Una frase compuesta o que no coincide por completo conserva el cerebro híbrido habitual.

## Motivo

La app nativa ya posee una fuente autoritativa y privada para saber qué aplicación está al frente.
Enviar la pregunta al daemon o inferir la respuesta añadiría latencia y podría exponer contexto
innecesario. Esta lectura puntual prepara futuras acciones contextuales sin concederles autoridad.

## Límites

- El nombre localizado es informativo y no constituye identidad, autorización o prueba de foco.
- Una aplicación puede carecer de nombre válido; Jarvis responde entonces con una negativa fija.
- No se enumeran aplicaciones abiertas ni se inspecciona la interfaz de la aplicación activa.
- No habilita órdenes ambiguas como «hazlo aquí»; esas acciones requieren un contrato separado.
