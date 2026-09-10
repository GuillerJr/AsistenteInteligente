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

En la comprobación local del arreglo, la aclaración tardó 2,15 s y el siguiente turno 4,33 s:
propuso registro de citas, notificaciones y panel de administración, seguido del diseño del flujo
de reserva. Son medidas de una ejecución, no un SLA. No se crearon archivos del proyecto.
