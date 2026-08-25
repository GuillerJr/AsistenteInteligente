# ADR-0071: Web pública y control de aplicaciones bajo política

- Estado: aceptado; control visual ampliado por ADR-0079
- Fases: 1, 4 y 5

## Decisión

Jarvis incorpora solo seis capacidades externas mínimas: investigar y leer texto HTTPS público,
listar metadatos recientes de Apple Mail, enviar un correo, listar eventos de Apple Calendar, crear
un evento y abrir una URL o aplicación instalada. No se incorpora un framework RPA ni acceso
arbitrario a AppleScript.

La lectura web valida DNS antes de cada salto, rechaza direcciones no globales, limita a tres
redirecciones, 512 KiB por respuesta y contenido HTML/texto. No hereda proxy, cookies ni sesión del
navegador. Mail lista remitente, asunto, fecha y estado de lectura, nunca el cuerpo.

Enviar correo, crear eventos y abrir apps/URLs se detiene en `awaiting_confirmation`. La concesión
está ligada al digest exacto y se consume una vez. JXA es código fijo recibido por `osascript` en
entrada estándar; los datos no aparecen en la línea de comandos y la salida está acotada. macOS TCC
mantiene el control final de Mail y Calendario.

## Consecuencias

- El enjambre puede investigar de forma autónoma sin controlar una sesión autenticada del usuario.
- El contenido web, correo y calendario es dato no confiable y nunca se interpreta como instrucción.
- Formularios, compras, borrado, edición de correo/eventos y automatización libre quedan fuera del
  MVP.
- Fase 1 asigna estas herramientas al planner; Fase 4 audita y confirma; Fase 5 reutiliza la ventana
  de aprobación existente.

ADR-0079 incorpora control visual acotado sin cambiar estas fronteras: no habilita formularios,
compras, borrado, correo ni automatización libre.
