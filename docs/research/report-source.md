# Fuente canónica interna — Investigación Jarvis/aegis

- Audiencia: propietario, arquitectos de sistemas macOS, seguridad y ML.
- Fecha de corte: 3 de septiembre de 2026.
- Plataforma objetivo: MacBook Air Apple Silicon sin ventilador; macOS 26 Tahoe como línea base.
- Alcance: resiliencia entre modelos, computer-use, GraphRAG, audio/biometría y seguridad local.
- Exclusiones: marketing no verificable, benchmarks de proveedores no reproducibles y APIs privadas de Apple.

## Respuesta ejecutiva directa

Jarvis puede convertirse en un agente local comercialmente defendible si el modelo deja de ser la autoridad. La arquitectura recomendada conserva razonamiento probabilístico como proponente, pero traslada selección de herramientas, validación de argumentos, flujo de datos, permisos, confirmaciones y verificación de efectos a un núcleo determinista. No existe evidencia que permita garantizar degradación cero al cambiar un modelo remoto de 120B por uno local de 3B; sí puede garantizarse que ambos operen bajo el mismo contrato, que una salida inválida nunca se ejecute y que la incapacidad termine en pregunta, escalamiento permitido o denegación segura.

## Supuestos y limitaciones materiales

1. El modo realmente offline no puede depender de NVIDIA NIM. NIM debe ser opcional y su ausencia no puede romper funciones esenciales.
2. La Foundation Models API de macOS 26 y la actualización multimodal documentada en junio de 2026 no son el mismo contrato de plataforma. Funciones de macOS 27 deben compilarse y activarse mediante disponibilidad, manteniendo Vision OCR en Tahoe.
3. App Sandbox estricto es incompatible con un asistente general que utiliza Accessibility y Apple Events arbitrarios. La distribución viable es Developer ID + Hardened Runtime + TCC + helpers de privilegio mínimo; una edición App Store tendría capacidades reducidas.
4. `PRAGMA secure_delete=ON` por sí solo no basta para la semántica interna de FTS5. Debe combinarse con la opción `secure-delete` de FTS5, cifrado de contenido y FileVault; ningún diseño de usuario puede prometer resistencia absoluta ante un host raíz comprometido.
5. Los objetivos de menos de 5 ms para memoria y menos de 300 ms para autorización biométrica son SLO que deben medirse en hardware real, no propiedades demostradas por la implementación actual.

## Matriz compacta de brechas

| Afirmación o decisión | Evidencia primaria | Confianza | Contradicción / límite | Resultado de diseño |
|---|---|---:|---|---|
| Guided generation garantiza forma estructural | Apple Foundation Models; Geng et al. EMNLP 2023 | Alta | No garantiza intención ni valores correctos | Grammar + schema + broker semántico |
| Un 3B puede sustituir sin degradación a 120B | Apple describe su modelo 3B como no orientado a razonamiento avanzado | Alta | La premisa es falsa | Contratos invariantes y abstención |
| Más contexto siempre ayuda | Lost in the Middle demuestra sesgo posicional | Alta | Depende de tarea/modelo | Context compiler pequeño y ordenado |
| AX + visión supera pixel-only en macOS | ScreenCaptureKit/Vision; UFO2; OmniParser | Alta | AX es incompleto en canvas/web | AX-first, OCR/region fallback |
| Hash exacto confirma transición visual | Evidencia del repositorio y teoría de imagen | Media-alta | Cambios irrelevantes alteran SHA-256 | Digest AX + dHash + postcondición |
| RRF k=60 es formalmente óptimo | Cormack et al. introduce RRF | Alta sobre RRF | k=60 es heurístico, no óptimo universal | Mantener como default y calibrar |
| secure_delete elimina todo rastro FTS | SQLite core y FTS5 docs | Alta | Requiere dos mecanismos distintos | PRAGMA + FTS5 secure-delete + cifrado |
| TTS negativos bastan para biometría | ASVspoof y NIST | Alta | No cubren humanos ni replay/deepfake | Dataset real + spoof + calibración ROC |
| Identidad fiable en <300 ms | Literatura de utterances cortas | Alta | Muy poca voz aumenta error | Decisión progresiva; Touch ID en alto riesgo |
| App Sandbox permite computer-use completo | Apple App Sandbox | Alta | Apple prohíbe Accessibility assistive APIs | Developer ID hardened, helpers separados |
| Prompt filtering soluciona inyección | AgentDojo, CaMeL, GitHub Security | Alta | Los modelos siguen siendo vulnerables | Separar control/datos y capabilities |

## Registro de fuentes y procedencia

| Fuente | Autor / editor | Fecha | Aplicación directa | URL / acceso |
|---|---|---|---|---|
| Meet the Foundation Models framework | Apple Developer, WWDC25 | 2025 | Modelo 3B 2-bit, guided generation, tool calling, límites de razonamiento | https://developer.apple.com/videos/play/wwdc2025/286/ (consultado 2026-09-03) |
| Foundation Models | Apple Developer | actualizado 2026 | API, sesiones, perfiles y multimodalidad versionada | https://developer.apple.com/documentation/FoundationModels (consultado 2026-09-03) |
| Foundation Models updates | Apple Developer | junio 2026 | Distingue novedades de plataforma posteriores a Tahoe | https://developer.apple.com/documentation/Updates/FoundationModels (consultado 2026-09-03) |
| Grammar-Constrained Decoding | Geng et al., EMNLP | 2023 | Garantía sintáctica mediante gramáticas | https://aclanthology.org/2023.emnlp-main.674/ (consultado 2026-09-03) |
| Lost in the Middle | Liu et al. | 2023 | Sesgo de posición en contextos largos | https://arxiv.org/abs/2307.03172 (consultado 2026-09-03) |
| BFCL V4 | UC Berkeley | actualizado 2026-04-12 | Evaluación AST, ejecutable, paralela y relevancia de herramientas | https://gorilla.cs.berkeley.edu/leaderboard.html (consultado 2026-09-03) |
| MCP tools specification | Model Context Protocol | 2025-11-25 | inputSchema/outputSchema y anotaciones de riesgo | https://modelcontextprotocol.io/specification/2025-11-25/server/tools (consultado 2026-09-03) |
| OSWorld | Xie et al. | 2024 | Benchmark de 369 tareas reales | https://arxiv.org/abs/2404.07972 (consultado 2026-09-03) |
| OSWorld 2.0 | Yuan et al. | 2026 | 108 flujos largos; evidencia de fallos de estado y verificación | https://arxiv.org/abs/2606.29537 (consultado 2026-09-03) |
| UFO2 | Zhang et al. | 2025 | HostAgent/AppAgent y fusión UIA-visión | https://arxiv.org/abs/2504.14603 (consultado 2026-09-03) |
| Cua | trycua | activo 2026 | Drivers, aislamiento VM y computer-use multi-OS | https://github.com/trycua/cua (consultado 2026-09-03) |
| OmniParser | Microsoft | activo 2026 | Parsing visual en regiones interactuables | https://github.com/microsoft/OmniParser (consultado 2026-09-03) |
| ScreenCaptureKit frame metadata | Apple Developer | actualizado 2026 | contentRect, screenRect, scaleFactor, dirtyRects | https://developer.apple.com/documentation/screencapturekit/scstreamframeinfo (consultado 2026-09-03) |
| Reciprocal Rank Fusion | Cormack, Clarke, Buettcher, SIGIR | 2009 | Fundamento de RRF | https://research.google/pubs/reciprocal-rank-fusion-outperforms-condorcet-and-individual-rank-learning-methods/ (consultado 2026-09-03) |
| SQLite FTS5 | SQLite Project | actualizado 2026 | rank/BM25, tablas sombra, opción secure-delete | https://www.sqlite.org/fts5.html (consultado 2026-09-03) |
| sqlite-vec | Alex Garcia | activo 2026 | Vector KNN embebido en SQLite | https://github.com/asg017/sqlite-vec (consultado 2026-09-03) |
| HippoRAG | Gutiérrez et al. | 2024 | PPR sobre grafo para memoria multi-hop | https://arxiv.org/abs/2405.14831 (consultado 2026-09-03) |
| MemGPT | Packer et al. | 2023 | Memoria jerárquica y gestión virtual del contexto | https://arxiv.org/abs/2310.08560 (consultado 2026-09-03) |
| Sound Analysis | Apple Developer | actualizado 2026 | SNAudioStreamAnalyzer y Core ML | https://developer.apple.com/documentation/SoundAnalysis (consultado 2026-09-03) |
| Silero VAD performance | snakers4 | 2024-2026 | Ventanas ~32 ms y latencia de inferencia | https://github.com/snakers4/silero-vad/wiki/Performance-Metrics (consultado 2026-09-03) |
| NIST SRE21 plan | NIST | 2021/2022 | Protocolo y calibración de speaker recognition | https://www.nist.gov/publications/nist-2021-speaker-recognition-evaluation-plan (consultado 2026-09-03) |
| ASVspoof 2021 | Consorcio ASVspoof | 2021 | Replay, logical access y deepfake | https://www.asvspoof.org/index2021.html (consultado 2026-09-03) |
| AgentDojo | Debenedetti et al., NeurIPS | 2024 | 97 tareas y 629 casos de inyección | https://arxiv.org/abs/2406.13352 (consultado 2026-09-03) |
| CaMeL | Debenedetti et al. | 2025 | Separación de control/datos y capabilities | https://arxiv.org/abs/2503.18813 (consultado 2026-09-03) |
| Safeguarding VS Code against prompt injections | GitHub Security Lab | 2025 | Tool sets, confirmaciones y límites del modelo | https://github.blog/security/vulnerability-research/safeguarding-vs-code-against-prompt-injections/ (consultado 2026-09-03) |
| SSRF Prevention Cheat Sheet | OWASP | actualizado 2026 | Allowlist, DNS y redirects | https://cheatsheetseries.owasp.org/cheatsheets/Server_Side_Request_Forgery_Prevention_Cheat_Sheet.html (consultado 2026-09-03) |
| Protecting user data with App Sandbox | Apple Developer | actualizado 2026 | Incompatibilidades explícitas con Accessibility y Apple Events arbitrarios | https://developer.apple.com/documentation/security/protecting-user-data-with-app-sandbox (consultado 2026-09-03) |
| HMAC, RFC 2104 | Krawczyk, Bellare, Canetti, IETF | 1997 | Construcción HMAC para IPC/logs | https://www.rfc-editor.org/rfc/rfc2104 (consultado 2026-09-03) |
| Secure Audit Logs | Kelsey y Schneier | 1999 | Integridad forward-secure y límites ante compromiso | https://www.schneier.com/academic/archives/1999/05/secure_audit_logs_to.html (consultado 2026-09-03) |

## Búsquedas realizadas y criterio de parada

Se realizaron olas separadas para: Foundation Models y schemas; benchmarks de tool calling; computer-use y grounding; RRF/PPR/memoria; audio/biometría; App Sandbox/TCC; prompt injection/SSRF; e integridad criptográfica. Se detuvo la recopilación cuando cada decisión crítica tuvo fuente primaria, las contradicciones de versión y sandbox quedaron resueltas y nuevas búsquedas devolvían duplicados o no alteraban la recomendación.
