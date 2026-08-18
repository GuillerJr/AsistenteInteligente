# ADR-0004: memoria persistente local y recuperación textual

- Estado: aceptado
- Fecha: 2026-08-18

## Contexto

El enjambre necesita recordar preferencias, hechos, episodios y resúmenes entre reinicios. Esa
persistencia no debe mezclarse con la cola efímera de trabajos ni conceder a los modelos una ruta de
escritura autónoma. Antes de añadir embeddings es necesario fijar contratos, aislamiento, límites y
propiedad del dato.

## Decisión

1. SQLite será la fuente de verdad local de la memoria estructurada. FTS5 ofrecerá una recuperación
   textual determinista mientras se incorpora la capa vectorial.
2. La base tendrá identidad de aplicación y versión de esquema explícitas. Aegis rechazará bases
   ajenas, versiones desconocidas, archivos no regulares, enlaces simbólicos, propietarios distintos
   y permisos accesibles por grupo u otros usuarios.
3. Cada registro pertenecerá a un namespace exacto. Las lecturas y búsquedas siempre requerirán ese
   namespace para evitar cruces accidentales de contexto.
4. Contenido, tags, consultas, número de resultados, extractos y capacidad total tendrán límites
   estrictos. Alcanzar la capacidad rechazará la escritura; nunca se expulsará memoria persistente de
   manera silenciosa.
5. Las búsquedas FTS se construirán únicamente desde tokens Unicode normalizados y se enviarán a
   SQLite como parámetros. Los operadores introducidos por el usuario no se interpretarán como SQL
   ni como sintaxis FTS.
6. `memory.put`, `memory.get`, `memory.search` y `memory.delete` se expondrán únicamente a través del
   IPC autenticado. Lectura y borrado exigirán la combinación exacta de namespace e ID. El acceso
   bloqueante a SQLite se ejecutará fuera del event loop del daemon.
7. La memoria no sustituirá a Keychain. Se rechazarán patrones inequívocos de credenciales y no se
   registrará contenido en logs de auditoría. SQLite operará con `trusted_schema=OFF` y los borrados
   habilitarán `secure_delete`.
8. En esta etapa las escrituras serán explícitas. Ningún agente podrá decidir por sí solo qué
   persistir; esa política se diseñará antes de conectar la memoria al grafo.

## Consecuencias

- Preferencias y contexto sobreviven al reinicio del daemon, mientras los jobs continúan siendo
  efímeros.
- El sistema dispone de un baseline de RAG local útil aun sin embeddings o conectividad externa.
- SQLite no cifra por sí mismo. La confidencialidad en reposo depende de los permisos POSIX y del
  cifrado de disco de macOS; las credenciales permanecen fuera de esta base.
- La capa vectorial adoptada posteriormente en ADR-0005 referencia `memory_id` y `content_sha256`
  sin cambiar el contrato público de los registros.
