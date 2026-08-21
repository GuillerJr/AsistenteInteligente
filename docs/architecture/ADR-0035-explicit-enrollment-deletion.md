# ADR-0035: Eliminación explícita de muestras de enrolamiento

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** exigir Finder para retirar grabaciones contradice el control local del usuario.
2. **Eliminar:** no se añade gestor de archivos, papelera propia, historial ni borrado automático.
3. **Simplificar:** una acción confirmada elimina todos los CAF reconocidos y reinicia los contadores.
4. **Acelerar:** el usuario puede corregir un dataset defectuoso sin comandos ni rutas manuales.
5. **Automatizar:** primero se valida el conjunto completo; cualquier anomalía cancela todo borrado.

## Decisión

La ventana de enrolamiento muestra `Eliminar muestras…` solo cuando existe al menos un clip. macOS
presenta una confirmación destructiva que aclara que un modelo entrenado no se elimina. La operación
queda bloqueada durante grabación, carga o una eliminación previa.

El core enumera y valida ambas clases antes de borrar el primer archivo. Solo admite CAF regulares,
privados, sin enlaces, dentro de los límites establecidos. Un elemento inesperado falla de forma
cerrada y conserva todas las muestras. Tras el borrado mantiene las carpetas `0700` y devuelve
contadores en cero.

## Consecuencias

El usuario obtiene una vía visible para retirar el dataset persistente sin ampliar el acceso al
sistema de archivos. La acción es irreversible y nunca implícita. El modelo empaquetado continúa
operativo hasta que el usuario lo reemplace mediante el flujo separado de entrenamiento.
