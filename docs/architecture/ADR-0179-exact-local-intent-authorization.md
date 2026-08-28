# ADR-0179: autorización para intención local exacta y reversible

## Estado

Aceptada. Reemplaza la confirmación obligatoria de `browser_search` definida en ADR-0177.

## Contexto

Una orden inequívoca como «Abre Safari» ya contiene aplicación, operación y autoridad del usuario.
Pedir otro clic añade latencia sin aclarar nada. Sin embargo, permitir que una llamada propuesta por
un modelo invoque la misma herramienta sin confirmación abriría una escalada de autoridad.

## Decisión

1. `ToolCall` distingue `model_proposed` de `explicit_local_intent`; el valor participa en el digest
   auditado y el parser NVIDIA nunca puede establecer la segunda base.
2. Solo el parser local cerrado emite intención explícita. Se rechazan negaciones, órdenes
   compuestas, aplicaciones desconocidas, audio no transcrito on-device y argumentos con secretos.
3. La excepción se limita a acciones reversibles y exactas: volumen/silencio, multimedia,
   `application_open`, `browser_open_url` y `browser_search`.
4. El Tool Broker y cada ejecutor verifican esa razón por separado. Las llamadas de modelo a esas
   herramientas conservan confirmación; Mail, Calendar, Contactos, Recordatorios, Spotlight abierto,
   Shortcuts, red, Terminal, control visual y plugins nunca admiten la excepción.
5. Autorización, ejecución y resultado siguen registrándose en la cadena de auditoría. Un éxito de
   LaunchServices solo prueba que macOS aceptó abrir el destino.

## Consecuencias

- Las órdenes cotidianas exactas responden en una sola interacción y sin inferencia.
- Un modelo, Skill, plugin o argumento no puede fabricar la base local ni ampliar su lista cerrada.
- Las acciones destructivas, persistentes, externas o ambiguas conservan consentimiento de un solo
  uso y fallan de forma cerrada.
