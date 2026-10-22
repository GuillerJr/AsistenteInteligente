# Dependencias complementarias de Codex — 22 de octubre de 2026

Continúa la [cuarta revisión](CODEX_REA_FEATURES.md) con 21 de los 27 destinos
externos al último bloque: módulos de Git/PR, catálogo documental, complementos,
presentación del navegador y utilidades. Las otras seis entradas son los tres
módulos centrales, dos módulos de voz ya revisados y un módulo decorativo de
iconos. Esto no convierte esas 27 entradas en el inventario total pendiente.

## Evidencia y alcance

El artefacto conserva la versión `26.1016.51333` y el SHA-256 de ASAR
`9d0260dd37f9c45546d5a89d9d782b8362c9957b70b992e2c3f91df60a046b07`.
Las 21 fuentes se extrajeron exactamente, verificando su integridad, a
`/private/tmp/codex-rea-dependencies-next-20261022`. No se ejecutó código de Codex,
no se accedió a historial ni credenciales y no se copió código o recursos al
repositorio de Jarvis.

REA 6.7.0 procesó individualmente **189.805 bytes y 55.557 nodos AST**, con 768 MiB
y 45 segundos por fuente. Las ejecuciones finales tienen cero errores de sintaxis,
cero fuentes omitidas y cero omisiones de nodos o relaciones por recursos. Retienen
34.736 nodos semánticos, 39.220 relaciones y 10.929 incógnitas estáticas; estas últimas
no son defectos. La cobertura semántica permanece desconocida y las 55 importaciones
literales no resuelven por sí mismas sus dependencias ni sus valores dinámicos.

El helper de control de procesos de REA no pudo compilar con Xcode dentro del
sandbox. Se detuvo ese lote y se conservaron los intentos incompletos aparte.
Las ejecuciones finales fuera del sandbox son las únicas contabilizadas. El CLI
permitió conservar cada evidencia completa por separado, sin ampliar el conjunto
de evidencia de la sesión MCP compartida. Tamaños y hashes de fuentes y evidencias
se verificaron de nuevo. El [manifiesto propio](CODEX_REA_DEPENDENCIES_NEXT_COVERAGE.json)
contiene identidades, rangos, importaciones, observaciones y límites; los 67.585.712
bytes de evidencia completa permanecen en el directorio temporal.

## Observaciones contrastadas

| Superficie | Hecho observado en texto estático | Criterio aplicado en Jarvis |
| --- | --- | --- |
| Catálogo documental | Valida estructura, generación, revisiones, identidades duplicadas y metadatos JSON acotados. La retirada de una edición exige su revisión exacta. | Un registro de complementos ilegible o de estructura incompleta no equivale a una lista vacía. |
| Consultas de complementos | Vincula conexiones a enlaces e identidad de app; espera invalidaciones y propaga un error de la consulta actualizada. | Un fallo de instalación conserva su estado de fallo y la caché se invalida incluso si el error ocurre después del reemplazo del archivo. |
| Git y preparación de PR | Las señales de cancelación están ligadas al contexto; una finalización solo retira su propia señal. Las consultas reciben la señal y comprueban cancelación después de esperas. | Después de descargar un marketplace se vuelve a leer el estado actual antes de publicarlo; dos descargas no sobrescriben mutuamente sus altas. |
| Presentación del navegador | `cookies-…` declara un icono SVG. | No se le atribuye una implementación de cookies ni se añade una función basándose en el nombre del archivo. |

Son criterios de diseño sobre implementaciones propias, no pruebas del servidor
de Codex ni una afirmación de equivalencia entre ambas aplicaciones.

## Cambios en Jarvis

- Los registros `installed.json` y `marketplaces.json` distinguen ausencia de
  corrupción, permisos inseguros, enlaces simbólicos, tamaño excesivo y estructura
  incompleta. Una lectura inválida devuelve un error controlado antes de alterar
  habilidades o descargar repositorios. Se comprueban identidades, pertenencia de
  habilidades, duplicados y los campos requeridos.
- Las escrituras usan `write_private_json`, con presupuesto explícito de 1 MiB;
  se conserva el registro anterior cuando la validación o la escritura previa al
  reemplazo falla. La lectura tolerante de manifiestos de terceros sigue separada
  de la lectura estricta del estado del dueño.
- La publicación de marketplaces serializa las mutaciones con instalaciones y
  bajas, vuelve a validar capacidad y registro después de la descarga y restaura
  el árbol anterior si falla antes de publicar sus metadatos. Si el reemplazo ya
  quedó confirmado en disco y falla la sincronización posterior, conserva el árbol
  que corresponde a esos metadatos y devuelve fallo, sin confirmar éxito.
- Solo los marketplaces registrados aportan complementos al catálogo. Una baja
  publica primero su registro; un fallo previo no borra el árbol. Un árbol residual
  tras un fallo posterior no reaparece como marketplace activo.
- Una instalación sin huella preparada o cuyo contenido cambió devuelve
  `success=false`, `verified=false` y un código de fallo. El trabajo y el historial
  ya no la presentan como ejecución exitosa. El trabajo aprobado usa el mensaje
  general existente de ejecución fallida; el recibo conserva la explicación.

## Pruebas

Las 44 pruebas de `tests/test_foreign_plugins.py` pasan con datos sintéticos y sin
red. Se reprodujeron los fallos antes de corregirlos. Cubren registros corruptos,
estructuras parciales, permisos inseguros, symlinks, tamaño, ausencia, instalación
aprobada, cambio de contenido, descargas solapadas, corrupción durante la espera y
fallos de publicación antes/después del reemplazo, límite de instalaciones y
validación del estado propuesto antes de escribirlo. `ruff` pasa en los archivos
modificados. Los controles completos y la instalación se verifican en el lote
integrado; este informe no los sustituye.
