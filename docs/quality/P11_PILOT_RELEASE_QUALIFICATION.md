# P11 — Promoción integral a piloto local

P11 responde una sola pregunta: ¿pertenecen todas las garantías de Jarvis al mismo código y están
listas para promover esa revisión a un piloto en el Mac del propietario? No vuelve a implementar
voz, visión, navegador o memoria. Compone sus pruebas sin rebajar ninguna.

## Compuerta

```bash
./script/p11_pilot_release_gate.sh
```

La secuencia es estricta:

1. exige un checkout tracked limpio y la misma revisión en `HEAD` y `~/Applications/Jarvis.app`;
2. ejecuta P10, que a su vez revalida P9, P8 y P7;
3. ejecuta la calificación física completa de macOS;
4. construye y verifica `dist/Jarvis.zip`, su manifiesto y la SBOM SPDX 2.3;
5. fusiona la evidencia en `dist/Jarvis.pilot.json` mediante escritura atómica privada.

Si P10 necesita la voz del propietario, P11 se detiene. No continúa con un resultado parcial ni
sustituye la interacción por TTS, una llamada IPC sintética o contadores editados.

La etapa de voz reúne diez muestras de la compilación actual. Es el mínimo que permite medir el
objetivo biométrico del 90% sin que un único seguimiento manos libres domine la tasa. Durante esa
serie, el helper nativo de Apple Foundation Models permanece vivo y precalentado: la latencia mide
generación útil, no el coste repetido de arrancar un proceso y reconstruir su sesión.

## Los cinco contratos

| Contrato | Condición de aprobación |
|---|---|
| `release.identity` | Checkout, app, P10, macOS, candidato y hash del árbol fuente coinciden. |
| `quality.owner_voice` | Los cinco contratos físicos de P10 aprobaron. |
| `quality.live_macos` | Seguridad, TCC, voz, control visual y resistencia aprobaron en hardware real. |
| `release.candidate` | SHA-256 y tamaño del ZIP, SHA-256 de la SBOM y manifiesto coinciden. |
| `privacy.release_boundary` | No hay contenido del usuario ni tráfico de red en la evidencia. |

El lector acepta únicamente archivos regulares propiedad del usuario y con modo `0600`. Rechaza
enlaces simbólicos, archivos sobredimensionados, esquemas desconocidos, resúmenes matemáticamente
incoherentes, checks duplicados, rutas inseguras y cualquier modelo biométrico privado dentro del
candidato.

## Artefacto resultante

`dist/Jarvis.pilot.json` conserva solamente:

- la revisión Git completa;
- hashes SHA-256 del ZIP y de cada evidencia de calificación;
- cinco estados y métricas booleanas o contadores acotados;
- declaraciones explícitas de privacidad con valor falso.

Cambiar un byte del ZIP, la SBOM o cualquiera de las evidencias invalida la promoción. El informe
es local, ignorado por Git y no se transmite.

## Límite comercial

P11 certifica un **piloto local**. No afirma que la aplicación esté autorizada para distribución
pública. Una entrega a terceros continúa exigiendo una identidad Developer ID válida, Hardened
Runtime, notarización aceptada por Apple y stapling verificado mediante `release_macos.sh notarize`.
