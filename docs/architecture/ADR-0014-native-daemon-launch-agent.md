# ADR-0014: Daemon persistente mediante LaunchAgent

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** `nohup`, Docker y un supervisor Python duplican capacidades nativas de macOS.
2. **Eliminar:** no se añaden framework, instalador gráfico, root daemon ni proceso intermedio.
3. **Simplificar:** un script controla `install`, `status` y `uninstall` con `launchctl`.
4. **Acelerar:** el LaunchAgent ejecuta directamente el Python arm64 de `.venv`.
5. **Automatizar:** `RunAtLoad` y `KeepAlive` arrancan y recuperan el daemon automáticamente.

## Decisión

`ai.aegis.daemon` se instala como LaunchAgent del usuario actual. Usa rutas absolutas, directorio de
logs `0700`, plist `0600`, `Umask 077` y el directorio del repositorio como workspace explícito.

El daemon conserva la responsabilidad de crear la credencial IPC en Keychain y proteger socket,
memoria y auditoría. El script no recibe ni imprime secretos. `uninstall` descarga el servicio y
elimina solo su plist; los datos del usuario permanecen intactos.

## Encaje en el roadmap

- **Fase 1:** hace persistente el orquestador local.
- **Fase 3:** permite que la Menu Bar App encuentre el socket sin arranque manual.
- **Fase 5:** establece la operación real en segundo plano antes de añadir el HUD.

## Consecuencia

El servicio queda ligado a la ruta de este checkout y a su `.venv`. Mover el proyecto exige ejecutar
de nuevo `install`; empaquetado independiente y actualización automática siguen fuera del MVP.
