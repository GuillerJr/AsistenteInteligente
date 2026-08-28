# ADR-0143: aceleración vectorial SQLite acotada

- Estado: reemplazado por ADR-0197

## Contexto

La memoria semántica decodificaba BLOB `float32` y calculaba cada coseno en Python. El límite de
2.000 registros protegía la latencia, pero podía excluir un recuerdo relevante por su antigüedad.
La capacidad configurada de la memoria es 50.000 registros.

## Decisión

1. Se fija `sqlite-vec` 0.1.8 y se usa únicamente su función estable
   `vec_distance_cosine` sobre la tabla existente.
2. No se adopta `vec0`, no se migra el esquema y no se duplican vectores. La base sigue siendo
   legible sin la dependencia.
3. La extensión se carga desde el entorno Python bloqueado, con `trusted_schema=OFF` y
   `query_only=ON`; la capacidad de cargar extensiones se deshabilita inmediatamente después.
4. La ruta acelerada puede recorrer los 50.000 registros permitidos y devuelve solo el límite
   solicitado.
5. Ausencia, incompatibilidad o error de carga activa el cálculo Python existente, acotado a los
   2.000 registros más recientes. FTS5 permanece disponible independientemente de ambos motores.
6. Dimensiones, hashes, puntuaciones finitas, namespace, caducidad y límites se siguen validando en
   el núcleo de Jarvis.

## Consecuencias

La recuperación semántica gana cobertura y mueve el bucle numérico a código compilado sin adoptar
un servidor vectorial ni un segundo formato de persistencia. La dependencia es pre-1.0, por lo que
queda fijada y su uso se limita a una función SQL; cualquier futura adopción de tablas virtuales
exigirá otro ADR y pruebas de migración, corrupción y portabilidad.
