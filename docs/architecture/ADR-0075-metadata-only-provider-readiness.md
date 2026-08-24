# ADR-0075: Disponibilidad del proveedor sin leer credenciales

- Estado: aceptado
- Fases: 1, 4 y 5

## Decisión

El daemon incorpora `provider.status` al IPC local autenticado. La respuesta solo identifica
`nvidia_nim` y uno de tres estados de credencial: `configured`, `missing` o `unavailable`.

En macOS, la comprobación ejecuta `security find-generic-password` con servicio y cuenta, sin la
opción `-w`, y descarta stdout y stderr. Por tanto, verifica metadatos de existencia sin recuperar,
registrar, copiar ni crear la API key. Una variable `NVIDIA_API_KEY` ya presente en el proceso se
considera configurada únicamente si cumple el formato mínimo esperado.

La Menu Bar consulta el estado dentro de su sondeo existente, sin añadir timers. La captura de voz
y la escucha de «Jarvis» permanecen cerradas salvo que el daemon esté en línea, la auditoría esté
íntegra y la credencial figure como configurada.

## Consecuencias

- La interfaz explica por qué el enjambre no puede atender una voz antes de abrir el micrófono.
- Ningún valor secreto cruza el IPC ni entra en el proceso gráfico.
- `configured` significa presencia local; no afirma vigencia, cuota ni conectividad. Los probes de
  NVIDIA continúan siendo acciones explícitas y separadas.
- Un fallo de Keychain se reduce a `unavailable` sin exponer detalles internos.
