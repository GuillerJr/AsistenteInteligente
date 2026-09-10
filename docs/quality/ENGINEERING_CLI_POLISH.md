# Pulido 1 — CLI de ingeniería

Base: `a6a7555`. Este bloque corresponde al **CLI**, no al pulido previo del runtime.

## Correcciones y criterios verificables

| Problema | Corrección / prueba |
|---|---|
| `readline` bloqueaba un hilo y dificultaba Ctrl-C | Editor asíncrono; PTY real con descarte, señal SIGINT y EOF |
| Pegado, historial y comandos poco utilizables | Pegado multilínea sin autoenvío; Alt+Enter, Tab, flechas e historial acotado |
| Un error de transporte cerraba la sesión | Errores contenidos por operación, sin trazas privadas ni reenvíos automáticos |
| Una espera fallida dejaba trabajo invisible | Un solo job identificado, `/resume` y `/cancel`; nueva solicitud bloqueada mientras esté pendiente |
| Ctrl-C no confirmaba el fin del job | `jobs.cancel` firmado, ID exacto, límite de cinco segundos y aviso explícito si no se verifica |
| Autorización interrumpía el seguimiento | Seguimiento hasta el resultado, sin emitir aprobaciones y con espera acotada de 500 ms |
| Streaming repetía contenido al desaparecer `partial_result` en el snapshot final | Prefijo conservado, revisiones explícitas, identidad y versión comprobadas |
| Salida no confiable podía controlar la terminal | Escape de C0/C1, ANSI/OSC52 y bidi, incluso fragmentados |
| Estado y errores contaminaban tuberías | stdout para respuesta; stderr para diagnósticos/procedencia; stdin como solicitud literal única |
| Comandos ignoraban argumentos sobrantes | Aridad estricta y cambio de workspace validado por el daemon antes de cambiar contexto |
| “Offline” parecía impedir toda nube | Investigación e inferencia se explican por separado; `local_only` para prohibir inferencia remota |
| “Completado” podía confundirse con verificación técnica | “Respuesta recibida”, origen del daemon y aviso de contenido no certificado |

## Pruebas

```bash
.venv/bin/python -m pytest tests/test_engineering.py tests/test_engineering_cli.py \
  tests/test_engineering_terminal_pty.py tests/test_cli_entrypoint.py
./script/jarvis.sh --inference-policy local_only
```

El test PTY lanza un CLI real contra un UDS autenticado desechable. La tarea se controla con un
peer determinista para verificar pegado, cancelación, historial y salida sin voz, modelos, Keychain
personal ni Internet. Las unidades cubren fallos de red, versiones/IDs falsos, cancelación no
confirmada, aprobación pendiente, secretos, límites y contenido malicioso. No equivalen a medir
la capacidad de programación de un LLM.

También se probó el CLI contra el daemon instalado: saludo determinista, `/status`, descarte del
borrador y salida limpia. La política térmica suspendida se conservó, no se desactivó para pasar
las pruebas.

## Hallazgo abierto: calidad del modelo

Una consulta técnica local sobre condiciones de carrera produjo una definición incorrecta y
referencias a archivos inexistentes en un workspace vacío. **La calidad técnica del modelo no está
certificada y este bloque no la declara resuelta.** Sustituir el motor o endurecer la evidencia del
orquestador corresponde al trabajo del cerebro, no a embellecer el terminal. El CLI ahora distingue
el éxito de la entrega de una respuesta de su veracidad; no usa el score conversacional como prueba
de corrección del código.

El CLI sigue siendo de lectura para repositorios autorizados. Aplicación de parches, ejecución
de tests del proyecto y Git requieren capacidades separadas y aprobadas. No se añadieron permisos,
modelos ni herramientas de ejecución en este pulido.

## Corrección posterior: conversación de requisitos en un workspace vacío

El flujo real `hola → una app` mostró una respuesta en inglés que recitaba metadatos y confundía
la ausencia de archivos con la imposibilidad de ayudar. El prompt de ingeniería daba prioridad a
la auditoría del inventario, incluso obligaba a encabezar respuestas con su carácter parcial, y el
contexto añadía indicaciones de voz, foco y aplicación activa ajenas a una conversación de diseño.

Se corrigieron las instrucciones para conversar en español, continuar las respuestas breves del
usuario, pedir un solo dato esencial si falta el objetivo y proponer un MVP cuando este ya está
definido. El contexto local conserva petición, historial acotado, evidencia de repositorio,
memoria pertinente y skills, sin los perfiles de voz/aplicación. No se cambia el broker ni se
añaden permisos de escritura. El contexto privado sigue excluido del envío remoto.

La regresión de contrato comprueba tanto inventarios completos como parciales. Una prueba nativa
optativa recorre `hola → una app → app web de reservas de barbería` con Apple Foundation Models,
una carpeta temporal vacía y un proveedor remoto que falla si recibe cualquier llamada:

```bash
AEGIS_RUN_LOCAL_ENGINEERING_PROBE=1 .venv/bin/pytest -q -s tests/test_engineering_live.py
```

No abre micrófono ni consulta recuerdos personales. Se ejecuta solo con autorización explícita,
fuera de los gates deterministas: una salida probabilística no debe convertir el pre-commit en
una prueba inestable. Sus respuestas sintéticas se revisan además manualmente; aprobar ese flujo
no certifica la precisión técnica general ni resuelve el hallazgo sobre condiciones de carrera.

En la primera comprobación local del prompt, la aclaración tardó 2,15 s y el siguiente turno 4,33 s:
propuso registro de citas, notificaciones y panel de administración, seguido del diseño del flujo
de reserva. Son medidas de una ejecución, no un SLA. No se crearon archivos del proyecto.

La repetición posterior en la **CLI instalada** detectó variación: tras preguntar el objetivo,
Apple inventó una app de hábitos. Por eso el arreglo final no depende solo del prompt:
`engineering_clarification` reconoce solicitudes iniciales sin propósito con una gramática
acotada y devuelve una sola pregunta determinista, sin inferencia ni herramientas. Solo omite
saludos o peticiones igualmente indefinidas al comprobar el historial; cualquier turno sustantivo
deja la interpretación al modelo. Las peticiones compuestas o con un propósito no coinciden.

La prueba final del grafo devolvió la aclaración en menos de 10 ms; el diseño posterior sí usó
Apple. Sus propuestas todavía pueden ser redundantes o poco precisas y no quedan certificadas
por este contrato de entrada. Los tests cubren el rechazo de coincidencias parciales, la
conservación de contexto y la ausencia de llamadas a modelos/herramientas en la aclaración.

## Sustitución de la regla de frases: comprensión y sesiones

La gramática descrita arriba quedó sustituida por ADR-0215. Nuevas pruebas reales mostraron
que reformular el prompt no bastaba: `arregla eso` inventaba un archivo de prueba; `web`
perdía parte del objetivo; el inventario contaminaba preguntas conceptuales.

Correcciones: historial nativo con roles, una sesión Apple por petición, referencia separada
del turno y selección estructurada de contexto bajo demanda. No se añaden respuestas
prefabricadas para cada frase. La política y permisos permanecen fuera de la decisión del LLM.

Validación del 10 de septiembre de 2026:

- Suite Python completa aprobada fuera del aislamiento (dentro, el entorno impedía crear UDS).
- 224 pruebas Swift aprobadas, incluidas cinco nuevas de contratos de conversación.
- Evaluación AFM real: **10/11 escenarios aprobados** con datos sintéticos y remoto prohibido.
  El proyecto sintético `FARO-7391` no aparece en una consulta posterior sin historial.
  `arregla eso` pregunta por el problema, `web` conserva citas, `la segunda` elige HTML,
  la corrección a veterinaria cambia el dominio, y un cambio de tema responde sobre el cielo.
- El escenario largo `hola → una app → reservas de barbería` **sigue fallando el criterio de
  iniciativa**: entiende el tema, pero puede pedir otra aclaración genérica en vez de proponer
  el primer diseño. No se relajó esa aserción ni se declara resuelto.
- Latencias observadas en esa ejecución: 1,74–6,42 s por respuesta de modelo; no son SLA.
  No hubo voz, accesos a recuerdos personales, ejecuciones de herramientas ni llamadas remotas.

```bash
AEGIS_RUN_LOCAL_ENGINEERING_PROBE=1 \
AEGIS_ENGINEERING_PROBE_HELPER=/private/tmp/aegis-menubar-build/out/Products/Debug/jarvis-local-brain \
  .venv/bin/pytest -q -s --tb=short tests/test_engineering_live.py
```

Las aserciones léxicas de estas pruebas son indicadores de regresión, no una certificación de
semántica. También se revisaron las respuestas: hay simplificaciones y respuestas poco proactivas.
Persisten el hallazgo técnico anterior y la necesidad de comparar motores locales con una
batería independiente antes de prometer comprensión o desarrollo general fiable.

## Refuerzo de contratos y lectura nativa (ADR-0216)

Se corrigieron negociación en frío, contaminación tras cancelación, saltos de historial,
inventario bloqueante/no acotado por directorios, rutas relativas y errores de entrada.
El helper 2.2 ahora propone lecturas reales al broker; no ejecuta archivos ni escribe código.
Se añadieron pruebas de subproyectos, credenciales en archivos, propuestas malformadas y
recuperación después de superar el contexto.

La regresión Python completa y 225 pruebas Swift pasaron. La evaluación nativa final tuvo
**10/12 escenarios aprobados**, con iniciativa y una referencia breve todavía fallando.
Se descartó una clasificación experimental que empeoró los resultados. No se certifica la
precisión técnica ni se cierra el objetivo de un CLI de ingeniería completo.

No se descargaron modelos adicionales: se pausó esa propuesta al revisar el margen real de
los 16 GiB de memoria unificada del equipo. Los detalles y límites están en ADR-0216.
