# Recuperación nativa e incidencias de arranque y aprobación

Este incremento corresponde a los puntos 1 y 2 solicitados el 26-10-2026.
Su alcance es el contrato local de recuperación ya implementado, sus pruebas
nativas y las incidencias de demora y descubrimiento de las decisiones. No cierra
la fase A completa ni adelanta la fase B.

## Matriz finita y oráculos

La [matriz nativa](PHASE_A_RECOVERY_NATIVE_MATRIX.md) distingue los siete perfiles
WAL/journal con los tres roles SQLite, nueve perfiles de reparación de prefijo
y ocho escenarios negativos. Cada caso conserva proyecciones SQL esperadas,
hashes y metadatos de las fuentes. La interfaz y las pruebas IPC no se presentan
como evidencia de recuperación forense arbitraria.

Se observaron las 16 revisiones en la app. Las publicaciones de todos los casos
usan pruebas nativas IPC con decisiones sintéticas; las decisiones reales del
dueño y las posteriores delegadas se registran separadamente. El prefijo truncado
de memoria se publicó y restauró en otro perfil desarmado; el verificador externo
comparó estado, memoria y tareas y conservó los originales.

## Fallos encontrados al ampliar las pruebas

| Fallo | Corrección y comprobación |
| --- | --- |
| El arranque normal enumera conversaciones y tareas. SQLite puede consumir el WAL/journal original antes de entrar en mantenimiento. | Modo explícito `--recovery-only`: arranque, supervisión y reconexión consultan sólo salud e identidad local. No cargan sesiones, tareas ni el proveedor. Oráculos físicos antes y después de los 16 arranques. |
| Cambiar de pestaña o cerrar Ajustes ocultaba una decisión todavía pendiente. | Aviso persistente, selección conservada por operación y acceso desde la ventana principal para volver al panel y a su tarjeta. |
| Al caducar el ticket desaparecía también la explicación de caducidad. | Estado de la decisión independiente de la existencia de un ticket válido; los botones sólo usan una autorización vigente. |
| Un resultado antiguo de Recuperación eclipsaba una caducidad nueva de Mantenimiento. | Orden de detección por identidad, prioridad de esperas e incertidumbre/custodia vivas y conservación de la identidad al llegar a terminal. La reproducción falló antes y pasó después. |
| La acción de apertura se obtenía desde `App` y no tenía una comprobación de apertura real desde el acceso principal. | Puente SwiftUI estrecho en una vista montada, registro y retirada por identidad y botón visible que usa la ruta del delegado. Su apertura y pestaña correcta se observaron en GUI05. No retiene `NSWindow` ni usa selectores privados. |
| Una lectura fallida después de perder el servicio conservaba el ticket anterior en la interfaz. | Retirar el ticket observado y conservar la operación como incierta. Una nueva lectura autoritativa puede reconciliar el mismo trabajo; no reenvía la decisión, no renueva el plazo ni fabrica un recibo. Las respuestas de una lectura anterior no invalidan una decisión más reciente. |
| El diagnóstico rechazaba un journal dañado pero sólo mostraba «Fallida», sin motivo. | Explicación del journal no validado y código de diagnóstico del DTO visible. No atribuye ausencia de efectos a un código ni convierte un resultado incierto en éxito. |

`--recovery-only` es un modo del cliente, no una sandbox ni una ACL nueva del
servidor. Los efectos de mantenimiento conservan la autoridad central y sus
revisiones exactas. El modo es incompatible con la apertura implícita de un
perfil restaurado; abrirlo y conectar una cuenta siguen siendo decisiones distintas.

La automatización de menús y del gesto físico del atajo global no se usa como
evidencia de una causa histórica. El clic CUA en el botón principal ejecuta
exactamente la ruta del delegado y acredita la apertura real de la escena y de
la operación correcta, tanto pendiente como caducada.

## Demora antes de ejecutar Jarvis

La [investigación de arranque](PHASE_A_STARTUP_ISOLATION.md) conserva la muestra
histórica y separa la atribución histórica de tres reproducciones controladas:
lectura de `pyvenv.cfg` bloqueada, hook de usuario y `PYTHONHOME` inválido.
Liberar sólo la lectura controlada hizo avanzar el mismo PID; el arranque
canónico y aislado alcanzó salud nativa en aproximadamente 0,314 segundos.

Se usa el mismo intérprete elegido por su ruta canónica, `-I -S -B -u`, arranque
de biblioteca estándar, fuente como argumento y directorio estable. No se busca
otro intérprete ni se interpola la ruta en código. El archivo o causa de sistema
exactos de la incidencia histórica no se pueden establecer con la evidencia
disponible; no se atribuyen retrospectivamente a FIFO, iCloud, memoria o SQLite.

## Caída e incertidumbre

Las pruebas interrumpen un proceso propio, conservan `interrupted_unknown` y
comprueban que no hay publicación ni repetición. El producto rechaza un socket
residual. El reinicio del fixture se hace después de una intervención explícita
que acredita la salida del hijo, toma el lease exclusivo y retira sólo el socket
con identidad, dueño y modo previamente registrados. No se afirma recuperación
automática ni se eliminan las evidencias de una publicación incierta.

## Evidencia final

El [registro integrado](PHASE_A_RECOVERY_NATIVE_CLOSEOUT.json) conserva las
identidades de las fuentes y bundles, resultados completos, observaciones GUI,
recibos de las operaciones y límites de cada método. Los controles de publicación
vuelven a comprobar el árbol completo sin cambios durante pre-push.
