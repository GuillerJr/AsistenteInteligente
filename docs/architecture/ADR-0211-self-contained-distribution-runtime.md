# ADR-0211: runtime de distribución autocontenido

- Estado: aceptado
- Fecha: 2026-09-08
- Responsable: Guillermo (`gzambrano27`)
- Bloque: P13

## Contexto

El ZIP P12 empaquetaba la interfaz Swift y sus helpers, pero el servicio Python del piloto se
instalaba mediante un LaunchAgent que apuntaba al checkout, a `.venv/bin/python` y a `src/`. Una
firma y una notarización correctas no convierten ese acoplamiento en un producto instalable. Otro Mac
podía abrir la interfaz, pero no disponía del orquestador, las políticas ni la memoria.

## Decisión

Las distribuciones incorporan un runtime Python arm64 `onedir` construido por PyInstaller 6.22.2.
Se conserva un directorio, en vez de un binario temporal `onefile`, para evitar extracción en cada
arranque, latencia adicional y escrituras repetidas al SSD. Los pesos MLX, Whisper y modelos
biométricos no se empaquetan.

`BundledDaemonSupervisor` arranca el ejecutable con APIs nativas, entorno reducido y workspace
privado. Los builds normales sin daemon embebido continúan usando el servicio externo de desarrollo;
los ZIP de distribución, en cambio, fallan inmediatamente si no incluyen el runtime.

El autodiagnóstico del daemon solo usa memoria. Importa el grafo completo de arranque y comprueba
arquitectura, estado congelado, ausencia de `PYTHONPATH`, FTS5, sqlite-vec y enlazado de MLX/OpenMP.
No abre sockets, consulta Keychain ni escribe bases de datos.

Se permiten únicamente enlaces simbólicos relativos que resuelven dentro de `Jarvis.app`, porque los
frameworks de macOS y el runtime Python los requieren. Enlaces absolutos, rotos, cíclicos o que
escapen del bundle se rechazan antes o después de la extracción.

## Invariantes

1. Ningún ZIP de distribución se genera sin daemon autocontenido.
2. La app distribuida no necesita el repositorio, `.venv`, `PYTHONPATH` ni `PYTHONHOME`.
3. Todo Mach-O anidado se firma antes de firmar el bundle exterior.
4. El runtime es arm64 y conserva aceleración SQLite/MLX sin incluir pesos ni datos personales.
5. El arranque usa un workspace privado y rechaza ejecutables o directorios enlazados/inseguros.
6. P13 no aprueba sin un P12 auténtico del mismo commit y del mismo ZIP.

Los builds locales ad-hoc o con el certificado autofirmado de Jarvis no poseen un Apple Team ID. En
ese único perfil, el ejecutable congelado recibe `disable-library-validation` para poder cargar sus
bibliotecas Python firmadas localmente. El perfil Developer ID no recibe esa excepción: P12 inspecciona
también los derechos del daemon y bloquea la distribución si encuentra ese permiso u otro derecho de
depuración prohibido.

## Consecuencias

El ZIP aumenta aproximadamente 260 MiB sin comprimir. A cambio, Jarvis pasa de “piloto ligado a una
carpeta fuente” a aplicación distribuible con cerebro propio. La actualización atómica, rollback y
canal firmado quedan como frontera separada para P14; no se mezclan con la prueba de autocontención.
