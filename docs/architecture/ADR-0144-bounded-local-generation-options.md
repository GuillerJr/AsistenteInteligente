# ADR-0144: opciones de generación local acotadas de extremo a extremo

- Estado: aceptado
- Fecha: 2026-08-27
- Fases: 1 y 3
- Extiende: ADR-0142

## Evidencia

`AppleLocalModelClient` aceptaba `max_tokens` y `temperature`, pero los eliminaba. El helper creaba
`GenerationOptions()` con valores por defecto, por lo que el límite calculado por LangGraph no tenía
efecto. En una solicitud sintética de veinte oraciones, el helper anterior ignoró un límite de 48
tokens y seguía activo después de 60 segundos. Con el contrato corregido terminó entre 3,36 y 3,96
segundos, con 188–193 caracteres y un evento final válido, en el Mac `arm64` objetivo. El binario
instalado también rechazó un límite de cero con código de uso inválido antes de inferir.

## Decisión

1. Python serializa `maximumResponseTokens` y `temperature` junto con instrucciones y prompt.
2. El cliente rechaza booleanos, valores no finitos, temperaturas fuera de 0...2 y presupuestos
   fuera de 1...4.096 antes de iniciar el proceso.
3. El helper Swift vuelve a validar el mismo contrato y lo entrega a
   `FoundationModels.GenerationOptions` en `streamResponse`.
4. Los valores pueden ser nulos para conservar llamadas internas sin override; LangGraph ya fija
   192 tokens para conversación casual y presupuestos explícitos para las demás rutas locales.
5. Se conserva el proceso efímero decidido en ADR-0142: mantenerlo residente no elimina la
   inferencia dominante y retendría memoria entre turnos.

## Consecuencias

La respuesta local respeta el presupuesto de latencia y longitud que ya aplicaba la ruta NVIDIA.
El límite se verifica en ambos lados del pipe y una entrada directa malformada falla antes de
invocar Foundation Models. Una respuesta puede terminar por presupuesto, pero nunca crecer sin el
techo solicitado por el orquestador.
