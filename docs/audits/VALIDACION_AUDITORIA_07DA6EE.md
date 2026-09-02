# Validación independiente de la auditoría del commit `07da6ee`

- Fecha de validación: 2026-09-02
- Alcance: afirmaciones contenidas en `auditoria-completa-07da6ee.md`
- Criterio: una afirmación se considera demostrada únicamente cuando el código y una prueba
  reproducible la respaldan. Los objetivos de latencia no se presentan como resultados medidos.

## Resultado ejecutivo

La arquitectura examinada conserva controles sólidos, pero el documento externo no constituye una
certificación de producción. Contiene seis afirmaciones confirmadas, una confirmada con matices, dos
no demostradas y dos incorrectas. La aceptación macOS real continúa bloqueada hasta que el daemon,
Keychain, TCC y la evidencia operativa estén disponibles simultáneamente en el Mac destino.

## Matriz de evidencia

| Afirmación | Estado | Evidencia verificable |
|---|---|---|
| El ledger conserva un máximo local de 16 MiB y detecta sustitución del archivo. | Confirmada | `HashChainAuditLog.DEFAULT_MAX_BYTES`, comprobación de inode, cadena SHA-256 y ancla de Keychain. |
| La autorización vocal conserva 512 hashes durante 15 minutos. | Confirmada | `VoiceConfirmationVerifier` usa un `OrderedDict` acotado y TTL monotónico; las pruebas rechazan replay. |
| El OCR local limita su contexto a 8.192 bytes. | Confirmada | `LocalOCRPerception.maximumContextBytes` y truncado previo a Foundation Models. |
| Chrome no usa fallback JXA. | Confirmada | `ChromeCDPController` acepta únicamente CDP loopback y falla cerrado si el puerto 9222 no responde. |
| La memoria semántica no calcula coseno mediante un fallback Python. | Confirmada | La inicialización exige `sqlite-vec`; sin la extensión falla cerrado y `vector_acceleration_available()` devuelve falso. |
| MCP selecciona el grupo de tools por bundle identifier. | Confirmada con matiz | La selección por aplicación es una consulta directa. El registro inicial clasifica definiciones por capability/nombre, pero no tokeniza el prompt del usuario. |
| RRF se calcula dentro de una consulta SQLite. | Confirmada | CTEs léxico y semántico, `ROW_NUMBER`, `vec_distance_cosine` y fórmula RRF con `k=60`. |
| RRF tarda menos de 5 ms. | No demostrada | No existe una medición de RRF aislada sobre el volumen objetivo. La aceptación observada midió operaciones de memoria entre 7 y 14 ms; no son equivalentes a un benchmark RRF. |
| La aprobación dual siempre termina en menos de 300 ms. | No demostrada | La primera ventana abre a 250 ms y avanza cada 128 ms, pero el resultado depende de audio, SoundAnalysis, Whisper y hardware. Los valores son diseño, no percentiles medidos. |
| No existe ningún WAV temporal. | Incorrecta | El camino conversacional normal usa PCM en memoria, pero `speech.synthesize` conserva un WAV privado, acotado y efímero por compatibilidad y diagnóstico, según ADR-0140. |
| El hook de Git estaba activo en el checkout auditado. | Incorrecta | `.githooks/pre-commit` estaba versionado, pero `core.hooksPath` no estaba configurado. Versionar e instalar son estados distintos. |

## Pruebas ejecutadas

```text
uv run --no-sync pytest -q \
  tests/test_tool_audit.py tests/test_audit_anchor.py \
  tests/test_voice_confirmation.py tests/test_chrome_cdp.py \
  tests/test_mcp_host_manager.py tests/test_mathematical_memory.py \
  tests/test_memory_store.py tests/test_acceptance_benchmark.py \
  tests/test_beta_readiness.py

Resultado: todas las pruebas pasaron.
```

```text
./script/aegis.sh acceptance-benchmark

Resultado: 25/25, score=100, gate_passed=true, network_calls=0.
```

```text
./script/aegis.sh macos-qualification

Resultado: status=blocked, error_code=qualification_unavailable.
```

El último resultado no demuestra una regresión y tampoco puede convertirse en un pase sintético:
la compuerta requiere el daemon real, la clave IPC, permisos TCC y evidencia privada generada por una
sesión operativa. Hasta obtener `score=100` en ese entorno, no debe afirmarse que el producto está
certificado para distribución o demostración autónoma.

## Correcciones aplicadas

1. `script/setup_git_gates.sh --check` diferencia de forma determinista un hook versionado de un
   hook realmente activo y no modifica configuración durante la inspección.
2. `script/setup_git_gates.sh --install` configura únicamente el repositorio actual mediante
   `git config --local`, también en worktrees válidos.
3. El manual explica la activación, inspección y requisito de calificación viva sin recomendar
   `--no-verify` ni fabricar evidencia TCC.
4. Se conserva el WAV de compatibilidad. Eliminarlo rompería un contrato probado sin beneficiar al
   recorrido PCM normal, que ya procesa el audio completamente en memoria.

## Condición para una certificación real

La calificación solo puede considerarse completa cuando, en el Mac objetivo, se cumplen juntos:

1. `./script/setup_git_gates.sh --check` informa `status=active`.
2. `./script/build_and_run.sh --verify` termina correctamente.
3. `./script/aegis.sh acceptance-benchmark` devuelve 25/25.
4. `./script/aegis.sh macos-qualification` devuelve `score=100` y `gate_passed=true` con permisos y
   acciones reales del propietario.
5. La identidad Developer ID y la notarización se validan por separado para el artefacto de
   distribución; una firma ad-hoc no sustituye esa prueba.
