# Mejoras funcionales y de memoria — 23-10-2026

Implementación posterior al contraste del informe técnico con `1a5340b`.
Se mantiene NVIDIA como único proveedor de inferencia; los nombres técnicos
`aegis`, los permisos y las aprobaciones conservan su contrato.

## Cambios

- Las decisiones usan los modelos por rol guardados en Ajustes. Guardarlos sigue
  requiriendo reiniciar el servicio, como indica la respuesta de configuración.
  El diagnóstico y la auditoría identifican un respaldo NVIDIA que realmente haya
  completado la respuesta, sin prompts ni errores del servidor sin filtrar.
- Redactar en el chat, confirmar y ordenar una lista pegada no habilitan por sí
  mismos herramientas de servicios. Los destinos explícitos y las tareas compuestas
  conservan su alcance. La selección tiene hasta 12 turnos dentro de 8 KiB.
- La búsqueda pública distingue desafíos anti-robots, respuestas no reconocidas,
  vacíos explícitos y páginas que no se pudieron verificar. El diagnóstico técnico
  solo contiene proveedor y categoría, nunca consultas ni URL.
- El chat usa una sola consulta embedding NVIDIA para RRF y PPR, con búsqueda
  léxica ante indisponibilidad. El backfill se arma después de responder al
  preflight autenticado; mantiene límites por ciclo y reanuda desde la cobertura
  almacenada, incluyendo recuerdos añadidos después del arranque.
- La identidad del índice incorpora encoder, versión de reducción y dimensiones.
  Nemotron 3.5 Embed produce 2048 componentes; se usa su prefijo Matryoshka de
  384 componentes, renormalizado. El índice rechaza vectores mayores en lugar de
  plegarlos. Las llamadas usan los modos `passage` y `query` correspondientes.
- Diagnóstico distingue `lexical_only`, `indexing`, `hybrid_ready` y `degraded`.
  Un índice vacío no se anuncia como preparado. Se conserva el aislamiento por
  namespace, caducidad, chats temporales y borrado, incluidos resultados en vuelo.
- El refuerzo de confianza es monótono y acotado, incluido el valor 1. La métrica
  del grafo separa semillas, vecindario, PPR y formato; el presupuesto utiliza el
  tiempo completo de esa búsqueda, no solo el cálculo numérico.

## Evidencia y comprobaciones

Las regresiones comprueban identidad de evidencia y posición frente a distractores,
una sola consulta de embeddings por recuperación, versiones incompatibles, reanudación,
índice vacío, degradación, recuperación, expiración, aislamiento, cancelación y borrado
concurrente. Los embeddings artificiales de estas pruebas evalúan el cableado y la
política; no certifican por sí mismos la calidad lingüística del encoder NVIDIA.

Los escenarios de selección se comprueban con preferencias distintas del registro,
respaldos simulados y casos positivos de acciones explícitas, además de los negativos.
Las pruebas Swift cubren decodificación compatible y presentación del respaldo y
de los estados de memoria.

La prueba real de enrutado pasó cinco escenarios sintéticos, sin ejecutar herramientas;
tres consultaron NVIDIA y los otros dos se resolvieron mediante límites deterministas.
Una consulta web pública mediante el broker obtuvo una fuente verificable.

La prueba de recuperación real usó cuatro recuerdos ficticios y dos paráfrasis:
el híbrido obtuvo Recall@1 de 1/2 y Recall@3 de 2/2; FTS5 obtuvo respectivamente
0/2 y 1/2 en la misma base. El índice quedó preparado con 6/6 nodos. Se observaron
ocho HTTP 500 en once intentos de transporte; las otras tres respuestas permitieron
indexar y consultar. Esta muestra mínima demuestra el recorrido, no precisión general
ni disponibilidad del servicio. Ninguna prueba leyó el historial personal del dueño.

Comandos de validación:

```bash
.venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/python -m pytest -p no:warnings
./script/test_native.sh
.venv/bin/python script/sync_localizations.py --check
```

El pre-push vuelve a ejecutar Python, Swift, paquete Release y contratos de aceptación,
producción y fiabilidad. La instalación firmada se hace después de ese gate sobre
un árbol limpio. El dueño escribe la contraseña del llavero en Terminal.

## Límites

Se observaron respuestas HTTP 500 intermitentes del endpoint de embeddings, incluso
con salidas nativas; por ello una prueba exitosa no garantiza disponibilidad futura.
La indisponibilidad conserva FTS5 y el estado degradado, sin proveedores locales.
El backfill reintenta sin bloquear el audio ni la interfaz.

Los recuerdos y el índice se almacenan en el Mac. Las consultas y fragmentos usados
para embeddings se envían a NVIDIA después de redactar patrones de secretos; eso
no anonimiza todo el texto personal. El manual y la referencia técnica lo explican.

No se afirma una mejora porcentual de latencia, un p99 certificado ni éxito universal
en tareas largas. No se añade un pool SQLite, otro proveedor ni un Hub 3D sin medir
su beneficio. El uso real de TextEdit con «Siempre» y revocación sigue pendiente
del dueño en R2-3; los fixtures no sustituyen esa aprobación.

Contrato del encoder y reducción Matryoshka:
[NVIDIA Nemotron 3.5 Embed](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3_5-embed-1b-infer).

## Seguimiento de TextEdit

La prueba real posterior con `74911e3` encontró una variante del destino que faltaba:
«en el documento nuevo y vacío de TextEdit». Se incorpora al clasificador compartido,
manteniendo el enlace al bundle, el objetivo completo y el bloqueo de Drive, incluso
con catálogo dinámico y habilidades. «Escribe aquí, no en TextEdit» conserva el
destino del chat, sin confundir el texto citado ni «No guardes» tras el destino.
El planificador recibe un ejemplo JSON de
`field_contains` con el literal solicitado; una propuesta con texto genérico o un
booleano sigue siendo denegada. El sondeo real de dos frases con NVIDIA produjo
dos propuestas que requieren aprobación; no ejecutó herramientas.

Dos intentos autorizados de la app fallaron antes de despachar acciones con
`ComputerVisionNotDispatchedError`. El control visual usa ahora `perception_only=True`,
una opción que ya desactiva el razonamiento interno de Omni para decisiones visuales
acotadas. No cambia modelos, proveedores, respaldos ni validaciones. Una comparación
con imagen y AX ficticios usó el mismo modelo Omni sin sustitución: pasó de
8,9 segundos y 336 tokens a 1,3 segundos y 30 tokens. Ese caso simple no reprodujo
el fallo original y no certifica todavía el recorrido real corregido. La integración
HTTP/controlador prueba también que `length` o `content_filter` no despachan acciones.

Una repetición posterior sí reprodujo una respuesta incompleta: sin el modo de
percepción respondió el respaldo ya configurado Muse con `length`, contenido vacío
y 256 tokens, en 23,7 segundos. Con ese modo respondió Omni con `stop`, 31 tokens,
sin razonamiento y una acción `focus` válida, en 1,09 segundos. No hubo ejecución
en ninguno de los sondeos. Esto acredita que un intento de sustitución puede
intervenir en el fallo; no identifica retrospectivamente la ruta del turno del dueño.
`fallback_status` solo acredita una sustitución completada con éxito: un valor nulo
no excluye un intento fallido de usar el respaldo.

El helper observa la ventana autorizada con ScreenCaptureKit y Accesibilidad,
renueva la observación tras actuar y mantiene las imágenes en memoria. Usa
fotogramas por paso; no implementa una transmisión continua de todo el escritorio.
El permiso «Siempre», su reutilización entre chats y la revocación se mantienen
abiertos en P1-5 hasta comprobarlos en la app instalada.

Comportamiento de razonamiento y presupuesto de Omni:
[NVIDIA NIM Omni API](https://docs.nvidia.com/nim/vision-language-models/1.7.0/examples/nemotron-3-nano-omni-30b-a3b-reasoning/api.html).
