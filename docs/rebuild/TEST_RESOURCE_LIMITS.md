# Pruebas con presupuesto de recursos

Decisión del dueño del 27-10-2026: no repetir las suites completas en este Mac.
Se cualifica cada commit en GitHub Actions, tanto en Ubuntu como en macOS. Las
pruebas locales son selecciones explícitas, pequeñas y supervisadas. Este cambio
no reduce los criterios funcionales de la fase A ni permite dar por aprobada una
CI que todavía no terminó.

## Incidente y límites de la atribución

Durante la actividad de pruebas se observaron episodios de caída de espacio libre
desde unos 175 GiB hasta aproximadamente 1,5 GiB. El dueño identificó su relación
con las pruebas. El inventario acotado halló 18,785 GiB asignados a temporales
propios de pruebas y desarrollo; se retiraron 2,862 GiB de cachés reproducibles
identificadas, conservando perfiles, fuentes, respaldos y evidencia.

El registro del sistema confirmó que `com.apple.TMCacheDelete` liberó
180.433.907.712 bytes. Eso prueba una liberación de almacenamiento retenido por
Time Machine, pero no identifica los archivos originales ni demuestra cuántos
bytes escribió cada prueba. No se borraron ni desactivaron los respaldos del
dueño. El informe acotado quedó en
`/private/tmp/jarvis-storage-diagnosis-20261027.json`.

La revisión `89021c6` aprobó antes de este cambio 2674 pruebas Python y 399 Swift
en la [CI de Ubuntu/macOS](https://github.com/GuillerJr/AsistenteInteligente/actions/runs/40546090306).
En Swift hubo una omisión declarada: Chrome real requiere opt-in. No se repite
esa suite completa en el Mac para validar el nuevo supervisor.

## Contrato del supervisor

`script/test_resource_guard.py` es el punto de entrada. Distingue `local` y `ci`;
una ejecución local exige selección y rechaza la suite completa. La admisión de
pytest se comprueba antes de crear fixtures. CI exige el checkout y SHA del job;
esa comprobación previene errores de invocación, no es una frontera de seguridad
frente a código del mismo usuario que manipule su entorno.

La ejecución local tiene una cuota física privada, un plazo, vigilancia del
espacio libre y salida acotada. Python usa una cuota física de 1 GiB, vigilancia
de hasta 768 MiB asignados y 180 s; Swift admite hasta 2 GiB y 600 s. Una ejecución que agota el
presupuesto falla; no se amplía la cuota ni se repite automáticamente.

Los recibos identifican la selección, el árbol completo comprobado y el motivo
de cierre. Cambiar HEAD, el índice o fuentes no ignoradas invalida la identidad
anterior. Un resultado focal no equivale a cualificación completa.

Los temporales pertenecen a una ejecución nueva. La limpieza exige identidad
física y cierre comprobado. La custodia incierta se conserva y se informa; no se
borra mediante un glob global. Las cachés reproducibles pueden retirarse tras
confirmar el cierre. Se conserva un diagnóstico acotado separado del corpus y
de las credenciales sintéticas.

El supervisor limita su proceso y recursos propios. No certifica el cierre de
descendientes que eludan deliberadamente su grupo ni el consumo total de otros
programas del Mac; el espacio libre del volumen también se vigila por esa razón.

## Flujo de validación

1. Fuentes y AST en RAM, Ruff sin caché e identidad del árbol en los hooks.
2. Selección focal local explícita bajo el supervisor.
3. Commit y push del árbol congelado, sin modificar ni instalar durante pre-push.
4. Suites completas, paquetes y bundle en CI para ese mismo commit.
5. Registrar el resultado remoto antes de afirmar que la cualificación terminó.

No se reinstala la app del dueño para comprobar estos controles operativos.

## Comprobación local del incremento

Pasaron **65 casos focales en 4,647 s**, con pico de temporales asignados de
**1.802.240 bytes**. La ejecución real usó APFS y Seatbelt, conservó la identidad
del árbol y confirmó cierre del grupo, desmontaje sin fuerza y retirada de su
imagen. Se verificó el recibo contra ese mismo árbol antes de editar esta nota.

- Recibo: `/private/tmp/jarvis-test-resource-501-68a1d7e5af41/jarvis-test-evidence-amheph8p/receipt.json`.
- SHA-256: `50a6f5bb6db804971df05a37ba064e6bcede5c2006f845c29ad2fc1bb5acf4c2`.
- Selección: pruebas del supervisor y admisión, más dos casos de metadatos de
  `test_clean_base.py`. La cualificación completa del commit sigue dependiendo
  de CI; esta selección no la sustituye.

Los intentos anteriores fallaron antes de crear fixtures y cerraron sus cuotas.
Permitieron corregir la consulta de señal al padre fuera de Seatbelt, el uso del
shim de Git que pedía licencia de Xcode y la diferencia entre ignores del HOME
real y el privado. Se usa Git real con configuración global/sistema desactivada
para esta comprobación, sin modificar la licencia ni preferencias del dueño.

El tercer problema hizo que un cálculo leyera involuntariamente bytes de
`.claude/settings.local.json` para su hash, sin mostrar ni conservar el contenido.
La ruta quedó excluida canónicamente en `.gitignore`; si aparece versionada, el
supervisor la rechaza antes de abrirla. Una regresión exige explícitamente cero
lecturas de ese archivo sintético. No se abrió de nuevo la configuración privada.

La revisión posterior encontró una incompatibilidad de arrays opcionales con
`/bin/bash` 3.2 de macOS. Se corrigieron ambos wrappers y pasaron **cuatro casos
adicionales** que ejecutan ese shell con un supervisor de stub, sin Swift real.
Su ejecución supervisada terminó en 1,636 s, pico de 57.344 bytes y cierre
confirmado. Recibo `jarvis-test-evidence-o8ii8v1h/receipt.json` en el mismo
registro privado; SHA-256
`1bc57adf71751c8a8726f0d43b41e0cc5308bd5cd4a530dca2370ad298b7f171`.
La salida ya acotada del supervisor se publica en el log de CI para poder
diagnosticar fallos después de limpiar el directorio de trabajo de ese runner.
