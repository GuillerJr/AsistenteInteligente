# Estabilización de skills y extensiones

Fecha: 2026-10-07.

## Cambios

Configuración incorpora gestión visual de skills y plugins: revisión completa, importación
desactivada, selección explícita de herramientas y recursos declarativos, autorización para NVIDIA
por huella, revocación, desactivación y retirada con copia privada. Los cambios de contenido
invalidan autorizaciones; los archivos editados en sitio se detectan sin reiniciar. Una política
corrupta o llena bloquea la importación antes de escribir o archivar el archivo aprendido.

La selección normaliza acentos, combina hasta tres procedimientos del mismo rol dentro del
presupuesto de contenido y mantiene la última tarea del chat durante seguimientos. Para consultas
inciertas existe un selector NVIDIA acotado; sus respuestas no conceden autoridad. Los
procedimientos sobreviven a la regeneración de respuestas, sin duplicar el cuerpo en los prompts.
La voz sin identidad privada verificada solo ve procedimientos públicos integrados.

Desactivar o retirar un plugin bloquea sus handlers inmediatamente y revalida disponibilidad entre
preflight, DNS, TLS y el envío. La revocación impide nuevos POST detectados antes del envío; no puede
deshacer una petición ya transmitida. Activar o actualizar reconstruye el catálogo al reiniciar.

## Flujos offline

`python -m aegis_core.skill_flow_benchmark` completa 39/39 flujos con HTTP NVIDIA simulado:
sin skills, integradas, aprendidas y plugins con y sin consentimiento. Cubre evidencia ejecutada,
continuidad, cambio de tema, fuentes contradictorias, presupuestos, propuestas falsificadas,
aprobación exacta, replay, revocación y lectura de código temporal. El reporte distingue cobertura
de calidad real y no publica procedimientos privados.

## Piloto NVIDIA real

El primer intento con los modelos de código configurados no obtuvo respuestas útiles: dos
solicitudes devolvieron indisponibilidad y las siguientes quedaron bloqueadas por el cliente.
El catálogo oficial respondió HTTP 200; los IDs DeepSeek V4 asignados a código no figuraban allí.
Se corrigió ese rol usando Nemotron 3.5 Lightning como principal y Nemotron 3 Super como alternativa,
ambos ya configurados. Las preferencias anteriores se conservaron en una copia privada.

La comparación se ejecutó con asignación temporal de Lightning y sin fallback, sin alterar las
preferencias durante el piloto. Hubo doce solicitudes HTTP 200, dos por tarea, con seis ejecuciones
del grafo. Los archivos eran sintéticos y temporales; no se ejecutó su código ni se consultaron
cuentas, documentos personales o servicios externos de herramientas.

| Tarea | Sin skill | Con skill | Resultado del criterio exacto |
| --- | ---: | ---: | --- |
| Acceso a lista vacía | 8.457 s | 11.114 s | Ambas pasan |
| Inventario 18 − 7 | 6.598 s | 2.452 s | Ambas pasan |
| Corrección 38 → 41 | 2.866 s | 2.421 s | Ambas fallan en la cita de línea |

Las seis lecturas quedaron verificadas por el ejecutor y el broker; las skills se seleccionaron
solo en sus variantes correspondientes. Cuatro respuestas cumplieron el JSON, los valores y la
cita exacta. En las dos restantes los valores fueron correctos, pero la cita no coincidió con el
criterio requerido. Una sola muestra por variante y estas tareas pequeñas no demuestran una
mejora general de precisión ni una reducción fiable de latencia. No se alteró el oráculo para
convertir esos fallos en éxitos.

## Verificación de instalación

El empaquetado inicial detectó un exceso en el ejecutable nativo. Se conserva el presupuesto
de 24 MiB por ejecutable y 440 MiB por bundle: el núcleo compartido usa optimización de tamaño
y excluye los proveedores nativos de lenguaje locales que el runtime NVIDIA-only no utiliza.
Se mantienen OCR, audio, activación, identidad y DTO de compatibilidad; el helper de lenguaje
local responde como no disponible. Los experimentos de borrado de tipos de vista y LTO no
aportaron ahorro y se descartaron.

La suite release detectó una especialización incorrecta del compilador al combinar referencias
parciales a `CharacterSet.contains` con `contains(where:)` y `allSatisfy`. El sanitizador de nombres
usa ahora un recorrido explícito; se conservan los oráculos originales y se añaden controles
Unicode y fronteras de 160 bytes. Se valida también la suite completa con optimización release.

La validación utiliza Ruff, la suite Python del hook obligatorio, las pruebas nativas y la firma
estricta del bundle. El chequeo de IPC se limita a revisión, integridad, disponibilidad de
credencial, conexión Telegram y contrato del catálogo; no lee contenido de Google ni envía
mensajes. La certificación física de voz P10/P11 sigue aplazada.
