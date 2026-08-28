# ADR-0190: confirmación hablada natural de apertura web

## Estado

Aceptada.

## Contexto

La aprobación de una fuente pública debe mostrar su URL exacta para que el usuario conozca el
destino. Sin embargo, el resultado del ejecutor reutilizaba esa representación técnica como
respuesta final. En un turno de voz, el sintetizador terminaba pronunciando una dirección larga
carácter por carácter después de abrirla.

## Decisión

1. La ventana de aprobación conserva la URL exacta y la autorización sigue ligada a ese valor.
2. Después de ejecutar, el gestor exige que la URL devuelta coincida exactamente con la autorizada.
3. El resultado conversacional es la frase determinista `Abrí la dirección web solicitada.` y no
   contiene la URL.
4. La auditoría conserva por separado la autorización y el resultado técnico del ejecutor. No se
   modifica ni reduce la evidencia de seguridad.
5. No se añade un segundo campo IPC, un modelo, una dependencia ni lógica especial en el cliente de
   voz: la separación ya existe entre aprobación, auditoría y resultado conversacional.

## Consecuencias

- Jarvis responde con voz natural y termina el turno sin recitar una URL.
- El usuario sigue revisando el destino completo antes de conceder autoridad.
- Una sustitución entre la URL autorizada y la reportada por el ejecutor falla de forma cerrada.
- El camino directo y el camino aprobado presentan la misma confirmación breve.
