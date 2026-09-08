# P13 — Runtime autocontenido e independiente del repositorio

P13 cierra una diferencia crítica entre una aplicación que funciona en el Mac de desarrollo y un
producto que puede funcionar en otro Mac. Antes de este bloque, `Jarvis.app` incluía la interfaz
nativa, pero el daemon instalado se ejecutaba desde `.venv` y `src/` dentro del checkout. Eso era
válido para desarrollo, no para distribución.

## Resultado implementado

La distribución contiene ahora un daemon arm64 congelado dentro de:

```text
Jarvis.app/Contents/Resources/Daemon/jarvis-daemon
```

El runtime incluye Python 3.11, FTS5, sqlite-vec y las bibliotecas necesarias para MLX Whisper. No
incluye pesos de modelos, credenciales, memorias, audio, capturas ni modelos biométricos del dueño.
Los modelos personales continúan en `~/Library/Application Support/Aegis` y se crean en el Mac de
cada propietario.

Al abrir una distribución, la app Swift arranca ese daemon directamente con `Process`; no usa shell,
`PYTHONPATH`, `PYTHONHOME`, el checkout ni `.venv`. Su directorio de trabajo inicial es privado:

```text
~/Library/Application Support/Aegis/Workspace
```

El ejecutable y ese directorio se rechazan si son enlaces simbólicos o tienen permisos inseguros.
Al cerrar la app, Swift envía terminación al daemon para que pueda sellar correctamente la cadena de
auditoría.

El instalador local usa el mismo runtime embebido. Los builds de desarrollo mantienen una excepción
de validación de bibliotecas limitada al daemon porque una firma local no tiene Apple Team ID; la
compuerta comercial P12 exige que esa excepción esté ausente en el artefacto Developer ID.

## Compuerta

La certificación completa se ejecuta así:

```bash
AEGIS_CODESIGN_IDENTITY="Developer ID Application: Nombre (TEAMID)" \
AEGIS_NOTARY_PROFILE="jarvis-notary" \
./script/p13_self_contained_runtime_gate.sh
```

P13 vuelve a ejecutar P12. Por ello, la prueba física P10, la promoción P11 y las credenciales de
Developer ID/notarización siguen siendo prerrequisitos reales; no se sustituyen por mocks.

## Los cinco contratos

| Contrato | Condición de aprobación |
|---|---|
| `release.p12_chain` | P12 obtuvo 100, pertenece al commit actual y autentica el mismo ZIP/manifiesto. |
| `runtime.embedded_daemon` | El ZIP contiene daemon, librería base y arquitectura arm64 con permisos seguros. |
| `runtime.cold_start` | El ejecutable importa el daemon completo y supera su autodiagnóstico de FTS5, sqlite-vec, MLX y OpenMP. |
| `runtime.source_independence` | El proceso confirma ejecución congelada y ausencia de `PYTHONPATH`. |
| `privacy.runtime_boundary` | El autodiagnóstico no usa red/disco y el paquete omite fuentes y modelos privados. |

El reporte final se publica atómicamente como `dist/Jarvis.runtime.json`, modo `0600`, únicamente
cuando los cinco contratos alcanzan 100.

## Verificación local realizada

`script/build_daemon_bundle.sh` genera el runtime con PyInstaller fijado por `uv.lock`. El
autodiagnóstico se ejecuta con un entorno mínimo y sin acceso a la fuente. En el Mac de desarrollo el
artefacto resultante es arm64 y ocupa aproximadamente 260 MiB sin pesos de modelos. Este tamaño es
un costo de distribución medido, no memoria residente permanente: los módulos pesados siguen siendo
cargados bajo demanda.

La certificación comercial de P13 permanece `qualification_pending` hasta finalizar P10/P11 y
aportar una identidad Developer ID y un perfil de notarización válidos.
