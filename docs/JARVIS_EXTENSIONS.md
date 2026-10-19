# Skills y plugins de Jarvis

## Gestión desde Configuración

En **Extensiones**, Skills muestra el origen, el tipo de tarea, la disponibilidad y la autorización
para NVIDIA. **Revisar instrucciones** muestra el procedimiento completo, los recursos, las
herramientas acotadas y la huella de contenido. Activar y autorizar son decisiones distintas.

Una skill personal o de un plugin solo aporta instrucciones a NVIDIA tras autorizar esa versión.
Cambiar cualquier campo del manifiesto invalida ese consentimiento. Revocar interrumpe el envío
en solicitudes posteriores; no recupera datos ya enviados. Los procedimientos públicos integrados
pueden enviarse sin consentimiento adicional. La voz sin identidad privada verificada no utiliza
procedimientos personales ni sus recursos.

**Importar…** acepta JSON de skill de Jarvis o `SKILL.md` con nombre y descripción en metadatos YAML
sencillos. Para Markdown se elige el rol, las frases o palabras de activación y las herramientas
que el broker ya ofrece. Los enlaces a recursos no se abren automáticamente. La revisión muestra
los campos o funciones que requieren adaptación; una skill con requisitos ejecutables permanece
desactivada hasta reimportar una versión declarativa. No hay instalación de paquetes ni ejecución
de scripts desde una skill.

Plugins acepta paquetes JSON declarativos de Jarvis. La revisión muestra los hosts, los riesgos y
las capacidades de herramientas, junto con instrucciones y recursos. Se importan desactivados.
Activar conexiones requiere **Aplicar y reiniciar**; después se revisa el consentimiento de sus
skills. Desactivar o retirar revoca sus handlers inmediatamente. Retirar o reemplazar guarda una
copia privada en el almacén de archivos de extensiones y conserva las credenciales del llavero.

La verificación de integridad comprueba el checksum y el sello HMAC local. No certifica al autor
del paquete. Los formatos de otros agentes pueden necesitar adaptación: Jarvis no es un cargador
universal de plugins, librerías, scripts ni instaladores. Para un servicio nuevo siguen siendo
necesarios un conector compatible, una cuenta, sus permisos y, cuando proceda, una credencial.

## Selección y límites

- Frases claras se seleccionan localmente, sin inferencia adicional.
- La selección admite hasta tres procedimientos compatibles del mismo rol y un presupuesto
  acotado de contenido completo. No se recorta un procedimiento para aprobarlo.
- «Continúa» usa el último objetivo del historial de ese chat, con el catálogo y las autorizaciones
  actuales. Un tema nuevo impide recuperar tareas anteriores. No hereda autoridad de ejecución.
- Para peticiones ambiguas existe un selector NVIDIA con catálogo acotado, tiempo máximo de
  1,5 segundos y cache en memoria. Sus IDs, versiones y roles se validan; ante un fallo continúa
  la ruta ordinaria sin conceder herramientas nuevas.
- Las instrucciones importadas, los recursos, los resultados de herramientas y los recuerdos se
  tratan como contexto no confiable. El broker conserva las políticas y aprobaciones por llamada.

## Catálogo integrado

Las ocho skills integradas cubren capacidades que Jarvis ya puede ejecutar. No instalan
dependencias, conceden permisos ni sustituyen la verificación del resultado de una herramienta.

| Skill | Aporte específico | Límite material |
| --- | --- | --- |
| Control experto del Mac | Elige acciones nativas o control visual y comprueba su resultado. | El control visual mantiene sus restricciones de aplicaciones y acciones. |
| Navegación web experta | Distingue investigación, lectura y apertura; conserva fuentes y cobertura. | No prueba funciones de una versión instalada solo con resultados de búsqueda. |
| Auditoría defensiva | Prioriza evidencia e impacto con diagnósticos y alcances acotados. | No admite comandos libres ni convierte una hipótesis en hallazgo confirmado. |
| Revisión táctica de código | Relaciona defectos, escenarios y citas de línea con el archivo leído. | No aplica parches ni afirma que ejecutó pruebas. |
| Productividad personal | Conserva cuenta, fechas, IDs y estado de cada paso de correo y agenda. | Cabeceras, borradores y resúmenes no equivalen a cuerpos leídos o mensajes enviados. |
| Google Workspace preciso | Guía Gmail, Calendar y Drive con los esquemas del peer y sus restricciones. | Gmail admite borradores; Drive conserva sus límites de texto y de modificación. |
| Lectura y análisis con evidencia | Usa adjuntos disponibles, distingue unidades, correcciones y datos faltantes. | Texto parcial no permite certificar un documento completo ni exportar archivos. |
| Diagnóstico del Mac | Consulta batería, almacenamiento, audio, red o rendimiento según el síntoma. | Diagnostica; no cambia preferencias ni identifica causas sin evidencia. |

Los procedimientos aprobados se conservan tanto al planear como al redactar la respuesta final.
Antes de sintetizar se comprueba de nuevo su versión, disponibilidad y consentimiento. Si uno deja
de poder compartirse, se retiran sus instrucciones y los análisis que podrían citarlas; se conserva
la evidencia de herramientas ya ejecutadas. Esta comprobación no recupera contenido previamente
enviado a NVIDIA.

Cuando un único especialista de código o análisis solicita y verifica una lectura, la respuesta
cerrada utiliza sus modelos NVIDIA configurados. Conserva el rol de salida del sintetizador,
sin otra ronda de herramientas ni cambios de preferencias. El razonamiento tiene presupuesto
acotado; las respuestas ordinarias conservan el recorrido rápido. Un objeto JSON solicitado
explícitamente activa su formato y se valida antes de publicar la respuesta del experto.
Los fragmentos pequeños ya saneados incorporan números de línea verificables, incluidas
cabeceras y líneas vacías. Si su disposición cambia durante el saneamiento, esa vista se omite.

Una skill no garantiza exactitud ni un porcentaje de éxito universal. Su utilidad depende de
selección, contexto suficiente, capacidades disponibles y respuesta del modelo; se mide con
resultados verificables y casos negativos, no con el número de procedimientos instalados.

## Evaluación

```bash
.venv/bin/python -m aegis_core.skill_flow_benchmark
```

El reporte compara sin skills, integradas, personales y plugins con y sin consentimiento. Sus
75 comprobaciones incluyen ejecución con evidencia, seguimiento, cambio de tema, fuentes
contradictorias,
presupuestos, herramientas falsificadas, revisión de código y aprobación exacta sin replay.
Utiliza el grafo, los prompts, el broker y el ejecutor reales, con respuestas HTTP NVIDIA y datos
sintéticos. Publica solo métricas agregadas, límites de payload y fallos por código.

Esta evaluación offline prueba el cableado y las restricciones. Sus puntuaciones no demuestran
que un modelo real razone mejor; las comparaciones remotas deben registrar los datos sintéticos,
el modelo, las llamadas y sus límites por separado. La certificación física de voz P10/P11 queda
fuera de este trabajo.

El piloto optativo `python -m aegis_core.skill_flow_live --run` utiliza la configuración NVIDIA
existente y seis ejecuciones del grafo sobre tres archivos temporales: acceso a una lista vacía,
aritmética de inventario y una corrección explícita. Compara cada caso con y sin skill, valida JSON,
valores y citas mediante oráculos independientes y exige evidencia de la lectura autorizada.
Limita el total a doce solicitudes HTTP, incluyendo reintentos y alternativas, y se detiene ante
429 o el límite global. Sin `--run` no accede al llavero ni a la red. El reporte no publica entradas,
respuestas o secretos y no representa una evaluación estadística general de calidad.

`--profile adversarial` añade defectos independientes y funciones protegidas, revisiones de tablas
con unidades diferentes, y correcciones del dueño frente a propuestas e instrucciones ajenas.
Usa las skills de producción, no procedimientos redactados para el examen. `--pilot-model ID`
solo permite un modelo ya configurado con herramientas; asigna temporalmente código y análisis,
sin guardar preferencias. Esta comparación incluye selección de rol y contexto, además del
procedimiento. Los controles que prueban rechazo o falta de alcance no se cuentan como tareas
completadas. Los resultados y sus límites se registran en
[la estabilización de extensiones](quality/EXTENSIONS_STABILIZATION.md).
