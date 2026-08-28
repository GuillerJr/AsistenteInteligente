# ADR-0191: resultados naturales ligados al alcance autorizado

## Estado

Aceptada.

## Contexto

Las acciones aprobadas de aplicación, búsqueda y control visual conservaban identificadores y
consultas necesarios para revisar el alcance. El resultado conversacional reutilizaba esos datos:
Jarvis podía pronunciar `com.apple.Safari` o repetir una consulta larga. Además, la validación final
de aplicación y control visual no comparaba todos los campos relevantes con la autorización.

## Decisión

1. La aprobación y la auditoría conservan el bundle ID, la consulta, la URL, el objetivo y el límite
   de pasos exactos.
2. El resultado de aplicación debe devolver el mismo bundle ID autorizado y solo las claves
   previstas antes de responder `Abrí la aplicación solicitada.`
3. El resultado de búsqueda debe coincidir con navegador y consulta, pero la respuesta hablada no
   repite la consulta.
4. El resultado de control visual debe coincidir con la aplicación autorizada, no exceder el límite
   de pasos y usar una combinación coherente de estado y motivo.
5. La respuesta conversacional de control visual explica éxito, límite o bloqueo sin pronunciar el
   bundle ID. No se añade un modelo ni una tabla de nombres de aplicaciones.

## Consecuencias

- La voz es más breve y natural sin reducir la evidencia visible o auditable.
- Un ejecutor que reporte otra aplicación, pasos adicionales o un estado incoherente falla cerrado.
- Los caminos directos y aprobados usan el mismo estilo de confirmación.
