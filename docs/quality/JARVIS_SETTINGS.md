# Configuración nativa de Jarvis

La ventana se abre con el engranaje del menubar o el comando nativo Configuración
(`⌘,`). Usa una barra lateral, las tarjetas de Jarvis y el contorno nativo.

## Controles conectados

- General: inicio de sesión mediante SMAppService, dos atajos globales y alertas.
- Cuentas: estado MCP, importación de perfiles privados revisados (desactivados),
  activación, pausa y retirada de perfiles.
- Google: correo, prueba de refresh/identidad sin consultar contenido, cambio de
  cuenta mediante OAuth en el navegador y desconexión con revocación.
- Modelos: clave NVIDIA en el llavero, catálogo propio, principal/alternativa por
  cada una de las siete tareas y prueba sintética de texto/herramientas/imagen.
  Los modelos asignados se deben reasignar antes de quitarlos del catálogo.
- Voz: ventanas existentes de perfiles y muestras, escucha con modelo local.
  Se mantiene fija la voz actual; no existe un selector de voces.
- Privacidad: permisos del Mac y búsqueda/eliminación individual de recuerdos
  del espacio configurado, con confirmación antes de eliminar.
- Dispositivos: estado de Home Assistant y acceso a conexiones. Los dispositivos
  se añaden y configuran en Home Assistant.
- Diagnóstico: servicio, proveedor, modelo local, identidad, palabra de activación,
  atajo, último código de error de voz y compilación.

## Persistencia y aplicación

model_preferences.json se guarda en Application Support/Aegis con permisos 0600
y reemplazo atómico. Solo admite catálogo/asignaciones validados; no admite
credenciales ni ajustes de voz. NVIDIA carga las preferencias al iniciar.

Los cambios de modelos/MCP requieren «Aplicar y reiniciar». La app espera la
respuesta del endpoint autenticado tras reiniciar el daemon. El botón se bloquea
durante voz, aprobaciones, grabación, entrenamiento y operaciones pendientes.
Cancelar una escritura de credenciales espera a que termine el trabajo del llavero
antes de admitir otra operación. La cancelación no revierte la escritura que ya
empezó; impide que ese worker pueda sobrescribir una clave guardada posteriormente.
SIGTERM se deriva a la terminación de AppKit para que el daemon termine y selle
la auditoría antes de cerrar la app.

## OAuth y acceso

Se usa el cliente existente, los mismos 13 alcances, state aleatorio y PKCE S256.
El callback escucha en 127.0.0.1:18731, expira en cinco minutos y acepta una sola
respuesta válida. Se comprueba el correo verificado con Google antes de guardar
tokens o cambiar el perfil. Los tokens usan el formato privado 0600 del peer.
Los secretos no se devuelven en estados, errores ni resultados.

La conexión se pausa al cambiar de cuenta. Cancelar o fallar conserva el perfil
anterior; aplicar y reiniciar restaura sus conexiones. Desconectar desactiva el
perfil, detiene el peer y revoca sus tokens.

settings.* pertenece al IPC autenticado y no se expone como herramientas del
agente. Se conservan las revisiones de esquemas/artefactos y aprobaciones MCP.
Importar no inicia procesos; activar requiere confirmación en la app.

## Validación

Pruebas de persistencia, consumo efectivo por NVIDIA, incompatibilidad de modelos,
rechazo de campos de voz, archivos inseguros, confirmación, cancelación y OAuth
con state/identidad incorrectos y callback repetido. OAuth utiliza respuestas
simuladas y las pruebas no cambian cuentas reales.
