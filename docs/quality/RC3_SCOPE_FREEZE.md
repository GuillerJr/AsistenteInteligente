# RC-3 — Congelación verificable del alcance 1.0

RC-3 convierte el alcance de Jarvis en un contrato ejecutable. La fuente canónica es
`packaging/JarvisV1Scope.json`; el comando `./script/aegis.sh release-scope` la valida sin red ni
datos del usuario.

## Decisión

- La línea `1.0-rc` contiene ocho capacidades declaradas.
- Cuatro están estables, tres dependen de proveedor o permiso local y una —voz— conserva su
  certificación física diferida a P10.
- Una capacidad nueva exige ADR, pruebas, actualización del modelo de amenazas y aprobación del
  propietario `gzambrano27`.
- Hasta cumplir esos cuatro controles, agregar proveedores, protocolos, superficies visuales,
  herramientas o polling de fondo está prohibido.

Esto no desactiva componentes existentes. Evita que el candidato vuelva a degradarse por *scope
creep* mientras se mide y corrige lo ya construido.

## Cambio de conexión autorizado el 23-10-2026

El dueño retiró expresamente la exclusividad NVIDIA y eligió Codex con su cuenta
de ChatGPT. [ADR-0235](../architecture/ADR-0235-configured-inference-and-codex-chatgpt.md)
registra la decisión y sus controles. El alcance ejecutable, modelo de amenazas y
pruebas se actualizan en el mismo cambio. Las certificaciones físicas pendientes
siguen abiertas; la voz queda sin configurar y no puede obtener una calificación
positiva a partir de evidencia anterior. Otros proveedores se incorporarán por
cambios posteriores; esta conexión no añade respaldos silenciosos.
