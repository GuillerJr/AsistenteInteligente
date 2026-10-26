# Continuación de recuperación — 26-10-2026

Implementación propia para seis bloques solicitados. La investigación REA
[OC-19](REA_PART19_SUMMARY.json) y [OC-20](REA_PART20_SUMMARY.json) añade cinco
fuentes fijadas de la referencia. No se ejecutó ni incorporó OpenClaw. Los
resultados estáticos conservan dependencias no revisadas e incógnitas.

## Contrato implementado

| Bloque | Comportamiento |
| --- | --- |
| WAL restante | Familia con WAL ausente, vacío o sólo cabecera válida: captura privada del estado durable presente. No inventa transacciones ausentes. Estado, memoria y tareas conservan sus originales. |
| Journal SQLite | DELETE/PERSIST caliente, con sectores, registros, páginas y checksums comprobados; PERSIST/TRUNCATE inactivo se descarta según el contrato SQLite, sin interpretar sus restos como transacciones. La recuperación ocurre en una familia privada. Journals ambiguos, truncados, multiarchivo/super-journal y familias mixtas se rechazan. |
| Workspace y artefactos | Archivo portátil separado del respaldo de perfil v1, con selección exacta, rutas relativas, hashes, límites e inventario. Restauración en carpeta nueva sin seleccionar ni ejecutar el resultado. Elegir una carpeta conserva esa carpeta y no descubre sus hijos. |
| Reparaciones | Reconstrucción del índice FTS de memoria desde filas base válidas; reparación explícita del prefijo WAL hasta el último commit verificado. Publica un respaldo reparado nuevo, utilizable por Verificar/Restaurar; nunca edita el original. El prefijo declara bytes descartados, pérdida posible y transacciones perdidas desconocidas. |
| Actualización | Paquete local de desarrollo con app nativa y fuente Python propias, inventario y hashes exactos. Requiere una copia verificada que coincida con el perfil y compatibilidad de SQLite y los recibos de mantenimiento. Selecciona la pareja mediante CURRENT y recibo durable. |
| Rollback | Selecciona la versión anterior verificada y compatible con los datos actuales. Conserva los datos; no aplica una migración inversa ni arranca programas automáticamente. |

La autoridad central liga cada efecto a operación, revisión, argumentos, perfil y
generación. Preparar una revisión sólo produce bytes privados y metadatos;
publicar requiere decisión del dueño. Los trabajadores conservan su corte hasta
terminar o acreditar la limpieza. Una publicación incierta no se repite.

La compatibilidad incluye los propósitos del registro de mantenimiento, además
de los tres esquemas SQLite. El empaquetador extrae el literal admitido por el
lector propio mediante AST acotado, sin ejecutar el código del artefacto. El
host comprueba el historial y las operaciones de actualización/rollback antes de
cada escritura; el launcher vuelve a comprobar el perfil cerrado antes del
arranque. Una base anterior que reconoce cuatro propósitos se rechaza ante los
nuevos recibos. La regresión se comprobó también con el snapshot propio antiguo,
sin ejecutar ese runtime ni conservar el candidato rechazado.

El UUID guardado en una copia identifica su procedencia; no obliga a conservar
la misma sesión del servicio después de reiniciar. La compatibilidad compara
el pin del perfil actual y todos los recursos del respaldo verificado. Los tres
SQLite de ambos lados se normalizan simétricamente mediante la API de backup
en RAM, después de inspeccionarlos, y se comparan completos. Los JSON conservan
comparación exacta. Los bytes y hashes del respaldo original se verifican sin
alterarlos; no se parchean cabeceras ni se omiten offsets para aceptar diferencias.

## Uso

El panel **Recuperación** prepara revisiones de archivos, reparaciones y versiones.
El panel **Mantenimiento** conserva la copia/verificación/restauración del perfil.
Para actualizar se crea y verifica antes una copia actual del perfil; la
actualización no crea otra oculta. La elección de credenciales, canales y memoria
conserva sus revisiones independientes.

`script/package_local_release.py` empaqueta únicamente un árbol Python propio y
un bundle Jarvis elegidos. `script/launch_local_release.py` muestra un plan por
defecto; el arranque explícito comprueba la pareja de la misma versión y exige
que el perfil anterior esté cerrado. No mata otros servicios.

## Límites que deben seguir visibles

No es recuperación forense de cualquier B-tree dañado ni de archivos borrados.
Se rechazan formatos ajenos, fuentes activas sin corte y corrupción de datos base
que no se puede reparar sin inventarlos. Las restricciones de permisos son
cooperativas; no aíslan código hostil con el mismo UID.

El paquete de versión usa el canal **development**: Python y Codex oficial son
dependencias externas. La autenticidad completa del paquete, distribución
autocontenida, Developer ID, notarización y un canal remoto firmado no quedan
acreditados por estos mecanismos locales. La instalación personal anterior no
se reemplaza para probarlos.

Los journals y WAL siguen el [formato de SQLite](https://www.sqlite.org/fileformat2.html)
y su [contrato de recuperación](https://www.sqlite.org/lockingv3.html). Una
familia ausente o incompleta no acredita que no existieran otras transacciones.

## Verificación

Las identidades, controles y lectura GUI acotada están en el
[registro de cualificación](PHASE_A_RECOVERY_COMPLETION_QUALIFICATION.json).

Los tests usan bases SQLite reales, procesos que dejan residuos controlados,
archivos propios, conexiones IPC y decisiones sintéticas. Las pruebas de cada
motor se distinguen de la integración del host y del recorrido nativo. El
registro de controles y las decisiones reales del dueño se documentan por
separado; compilar o aprobar un fixture no cierra toda la fase A.

La revisión final pasó **2297 pruebas Python y 249 Swift**, Ruff, compilación,
paquetes sdist/wheel y smoke test. Los conjuntos focalizados se solapan con esas
suites y no se suman a ellas.

En la app se completaron, con decisiones reales del dueño y datos sintéticos:
copia y restauración de dos archivos (283 bytes), respaldo de memoria con journal
caliente (65 notas), reparación FTS y restauración de una nota, e instalación
v1 → actualización v2 → rollback v1. Los hashes y originales se comprobaron por
otra lectura. El paquete v1 seleccionado arrancó realmente con su app y su fuente
Python de la misma versión, sin cuenta conectada, y cerró correctamente. Las dos
versiones positivas son fixtures propios del mismo código final con marcadores
de versión distintos; no acreditan una migración entre versiones productivas
arbitrarias. Una base propia antigua incompatible fue rechazada sin ejecutarla.

Las variantes WAL, reparación de prefijo y negativos de carreras/cancelación se
prueban automáticamente; no se afirma haber repetido cada variante desde la GUI.
La política de ventanas tiene seis pruebas de selección. La causa del incidente
anterior de una tarjeta que desaparecía no quedó establecida; no se atribuye a
ese cambio por el mero hecho de que el nuevo recorrido terminó.
