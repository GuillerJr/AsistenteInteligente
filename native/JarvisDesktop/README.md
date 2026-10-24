# Jarvis nativo — cliente de desarrollo

Implementación propia SwiftUI, macOS 14 o posterior, Apple Silicon, sin recursos
ni runtime OpenClaw. Ventana, Dock, barra de menús y atajo global configurable
(⌘⇧J o ⌥Espacio) comparten una instancia del servicio.

Desde la raíz del repositorio:

```bash
./script/test_desktop.sh
./script/build_desktop.sh
```

El segundo comando crea y firma ad hoc un bundle nuevo en `/private/tmp`; no
instala ni abre ninguna app. No es un paquete autónomo de distribución: requiere
Python, las fuentes y el cliente oficial Codex externos. Para una prueba manual
autorizada, usa la ruta del bundle devuelta y cuatro rutas absolutas explícitas:

```bash
open -n /private/tmp/RUTA/Jarvis.app --args \
  --python /RUTA/AL/REPO/.venv/bin/python \
  --source /RUTA/AL/REPO/src \
  --state /private/tmp/ESTADO-NUEVO-PRIVADO \
  --codex /RUTA/AL/CLIENTE-OFICIAL/codex
```

El directorio de estado debe ser nuevo o propio con permisos `0700`, sin enlaces.
No apuntar al estado del Jarvis anterior ni al historial personal. El cliente
inicia `python -m jarvis_core.service --state … --codex …`, supervisa su proceso y
solicita su cierre al salir. La inferencia usa exclusivamente Codex con ChatGPT;
el dueño completa el acceso oficial en su navegador.

El arranque admite hasta 30 segundos para la primera importación del servicio y
consulta su disponibilidad con llamadas breves de solo lectura. Si falla, conserva
el error específico; no lanza otro proceso ni adopta el servicio de otra instancia.
Las llamadas normales tienen un plazo total de hasta 30 segundos, compatible con
el plazo de 20 segundos del cliente oficial. El I/O usa un reloj monotónico y no
renueva el plazo cada vez que llega un fragmento.

Cada RPC usa socket Unix, token privado `0600`, JSON-RPC 2.0 con límite de 1 MiB e
identidad de solicitud comprobada. No hay listener web. Esta autenticación no
acredita identidad firmada del cliente macOS, TCC ni permiso para acciones reales.
Cancelar solo solicita cancelación; el cliente espera un estado terminal antes
de habilitar otra consulta. No se repiten automáticamente mutaciones fallidas.

Las pruebas Swift verifican framing, errores, configuración y conexión real al
servidor Python con estado sintético temporal. También arrancan y cierran la CLI
real tras un retraso sintético de seis segundos y reciben una respuesta RPC que
tarda cuatro segundos, para cubrir el arranque y los plazos del cliente.
No sustituyen la verificación
manual de ventanas, Dock, atajo ni una conversación real con la cuenta del dueño.
La distribución autónoma, notarización y módulos específicos Jarvis permanecen
fuera de esta implementación.
