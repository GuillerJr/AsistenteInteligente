# ADR-0202: Contexto técnico veraz y cierre de P4

- Estado: aceptado
- Fecha: 2026-09-07
- Prioridad: P4 — coherencia beta y recorrido de ingeniería
- Responsable físico: Guillermo (`gzambrano27`)

## Contexto

P3 vinculó la evidencia operativa con una revisión Git, pero dejó dos contratos de producto
incoherentes. El script beta todavía esperaba un snapshot anterior sin `build_revision`; por ello
una instalación sana fallaba como `invalid_readiness_schema`. A la vez, la CLI entregaba al modelo
los primeros 64 paths encontrados por un recorrido en profundidad. En este repositorio `docs/`
agotaba la muestra y una consulta real concluyó falsamente que no existían fuentes ejecutables.

El fallo no se corrige ampliando tokens ni cambiando de modelo. La causa era que el contexto
producido por el sistema representaba una fracción sesgada como si fuese el repositorio completo.

## Decisión

1. `runtime-readiness.json` evoluciona al esquema `2.0`; `build_revision` es obligatorio.
2. La compuerta beta compara la revisión exacta del checkout, `Jarvis.app` y el snapshot privado.
   Una diferencia se informa como `native_build_mismatch` y nunca se tolera por prefijo ambiguo.
3. La CLI escanea como máximo 4.096 rutas y conserva como máximo 64 en el prompt. La muestra se
   distribuye en ronda entre los componentes superiores para evitar que uno monopolice el límite.
4. El inventario publica `observed_file_count`, conteos por componente y `sample_complete`. El
   modelo tiene prohibido inferir ausencia cuando `sample_complete=false`.
5. El inventario sólo entra en el contexto local. El payload remoto no recibe paths del workspace.
6. Las afirmaciones sobre contenido exigen una lectura confinada mediante
   `filesystem_read_text`; una lista de nombres sólo demuestra estructura.
7. El stream nativo limita cada evento, el resultado final, el número de eventos y el tiempo total.
   Como cada snapshot repite el prefijo ya recibido, sólo el crecimiento monotónico representa
   contenido nuevo; sumar de nuevo el prefijo produciría un falso crecimiento cuadrático.
8. Una sesión `local_only` de ingeniería recibe un contrato de respuesta de hasta 220 palabras y
   genera como máximo 512 tokens por turno. El backend híbrido conserva hasta 4.096 para
   especialistas, pero el modelo 3B local no recibe ese coste por defecto en un MacBook Air sin
   ventilador.

## Invariantes de aceptación

1. Un repositorio grande con `docs`, `native`, `src` y `tests` aporta rutas de los cuatro grupos.
2. Directorios internos, cachés, builds, enlaces simbólicos y `.vscode` no entran en la muestra.
3. Una muestra truncada nunca se marca como completa.
4. El contexto de un proveedor remoto no contiene el inventario del repositorio.
5. El lector beta rechaza esquemas anteriores, revisiones no canónicas y builds desalineados.
6. Las pruebas Python, Swift y de aceptación continúan sin llamadas reales a proveedores.
7. Un stream acumulativo válido que supera 256 KiB de transporte repetido conserva un resultado
   final acotado; un evento o número de eventos fuera de límites falla cerrado.

## Consecuencias

P4 queda cerrado como frontera de coherencia beta: la CLI puede describir la estructura observada
sin confundir una muestra con evidencia exhaustiva, y la instalación ya no combina código,
snapshot y binario de revisiones distintas. Esto no convierte a la CLI en un editor autónomo ni
certifica voz/control reales; esas capacidades conservan sus aprobaciones y compuertas de hardware.
