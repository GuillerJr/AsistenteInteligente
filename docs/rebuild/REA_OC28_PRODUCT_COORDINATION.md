# OC-28 — producto, coordinación y proyectos

Referencia sólo de lectura: commit `1e3ff306abaff29f03e7668d53b01c07f10294ac`,
árbol `197c70097790e30087bbef323b0f2cc5148ab9b7` de OpenClaw. Se adquirieron
15 blobs exactos (182.916 bytes) en tres particiones privadas, sin ejecutar,
instalar ni importar el código en Jarvis. El manifiesto conserva ruta original,
tamaño y SHA-256 de cada blob. Los JSON adyacentes son proyecciones de evidencia,
no código activo. La autoridad `shipped-artifact` del extractor describe sus bytes
de entrada; aquí se trata de una referencia histórica de fuentes seleccionadas.

| Partición | Evidence padre | Contratos examinados |
| --- | --- | --- |
| Producto | `ev_bd297cef6b24c9b5d5b58f5460d92ad26ca8d4faae7da7a735e20d5e82213720` | Registro de proyectos/worktrees, pregunta con identidad admitida, espera/resolución y presencia filtrada por acceso. |
| Coordinación | `ev_5d59772b29a423c494be32b4042d5c04f7fcddc9ad3f400160250aff9d9a082b` | Asociación proyecto/sesión, destinos A2A capturados, entrega aceptada frente a confirmada y atribución del remitente. |
| Worktrees | `ev_235cae7796bc600835d3e2d8a88a93a037072e5c6cafb140585be591238ab665` | Protección del dueño, política de checkout, publicación de registros, snapshots exactos y recuperación de retirada. |

La selección es finita: 5 fuentes por lote, 256 MiB de heap y 20 s por fuente.
REA parseó 15/15 sin errores; la semántica sigue parcial, con incógnitas dinámicas
y dependencias fuera de cada partición. Ninguna página agotada demuestra revisión
del repositorio completo, equivalencia de ejecución ni paridad productiva.

## Decisiones propias y pruebas causales

- Las preguntas propias separan identidad admitida de campos de correlación.
  Respuestas en RAM al turno exacto; caducidad/revocación/cancelación destruyen
  el canal. No implementamos almacenes de secretos desde preguntas del modelo.
- Una sesión fija workspace físico y cliente. Invitación, membresía, agrupación
  y fijado usan CAS; un cambio de ruta no hereda colaboradores ni permisos.
- A2A local conserva solicitud, correlación, remitente, destinatario, plazo y
  estado. Recibir/confirmar un mensaje nunca inicia inferencia ni autoriza tools.
  Los efectos aceptados sin confirmación permanecen inciertos, sin retry oculto.
- Git usa lecturas literales y materialización propia de blobs; no checkout,
  filtros clean/smudge, hooks, fsmonitor o configuración ambiental del dueño.
  Archivar conserva snapshot antes de retirar únicamente un worktree propio.
- Los paneles son datos declarativos nativos, sin HTML/JS. Fuentes internas se
  resuelven con ACL vigente, no mediante rutas elegidas por contenido externo.
- Presupuesto local es admisión/reserva y conciliación del contador acumulado
  de Codex, con cancelación al observar agotamiento. No es un tope de facturación
  impuesto por el proveedor: no se inventan precios, uso ausente ni límites SDK.

Los módulos, pruebas, conexión nativa y cualificación se registran en
`PHASE_A_PRODUCT_COORDINATION.md`. Estos hallazgos guían contratos propios;
no se ha copiado implementación ni recursos de la referencia.
