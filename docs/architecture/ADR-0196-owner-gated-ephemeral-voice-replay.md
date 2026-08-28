# ADR-0196: repetición de voz efímera y ligada al propietario

## Estado

Aceptada.

## Contexto

Una orden como «repite» se enviaba al cerebro y podía producir texto nuevo, consumir red y alterar la
conversación. Repetir una respuesta privada sin comprobar al hablante también expondría correo,
agenda u otros datos ya pronunciados.

## Decisión

1. Un parser exacto reconoce solo órdenes inequívocas de repetición.
2. El último texto hablado se normaliza y conserva en RAM durante cinco minutos, hasta 2.000
   caracteres.
3. La repetición usa `AVSpeechSynthesizer`; no crea job, turno persistente ni llamada NVIDIA.
4. Si existe contenido, se exige perfil de voz del propietario y presencia local vigente.
5. El búfer se borra al expirar, retroceder el reloj monotónico, dormir, bloquear o reiniciar la
   conversación.

## Consecuencias

«Repite» devuelve exactamente la salida anterior con latencia local y sin duplicar inferencia o
memoria. La respuesta no sobrevive al proceso ni a transiciones de privacidad, y un hablante no
verificado solo recibe una negativa genérica.
