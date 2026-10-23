# Jarvis

Asistente personal para **macOS Apple Silicon**. Conversa por texto, organiza
proyectos y tareas, y trabaja con tus apps mediante acciones que tú apruebas.
La interfaz está en español. La inferencia usa **Codex con tu cuenta de ChatGPT**
y requiere internet; la ejecución de herramientas, los permisos y la memoria
cifrada viven en el Mac. Los embeddings se calculan localmente con Apple.
La salida de voz y las notas de audio de Telegram están pendientes de configurar
tras retirar NVIDIA NIM. Ollama y otras APIs quedan para conexiones posteriores.

## Instalar y empezar

Consulta el [manual único de instalación y uso](docs/MANUAL_INSTALACION_CONFIGURACION.md).
Incluye los requisitos, la instalación desde código, la bienvenida, los permisos
opcionales y el diagnóstico. La distribución pública todavía requiere Developer ID
y notarización: [estado de la 1.0](docs/V1_PENDIENTES.md).

Con el entorno de desarrollo preparado:

```bash
./script/setup_git_gates.sh
./script/menu_bar_service.sh install
```

Abre Jarvis y entra en Ajustes › Modelos. Pulsa «Iniciar sesión con ChatGPT» y
abre el enlace oficial; completa tú el inicio de sesión en el navegador. Elige
un modelo disponible y pulsa «Probar conexión» antes de tu primera pregunta.
No hace falta introducir una clave de NVIDIA. Puedes saltar los permisos opcionales.
Ajustes › General permite volver a abrir la bienvenida.

## Desarrollar

El daemon Python está en `src/aegis_core`; la app SwiftUI y los helpers están en
`native/AegisAudio`. El namespace `aegis` se conserva para mantener las rutas,
credenciales y permisos existentes. Las reglas están en [AGENTS.md](AGENTS.md).

```bash
.venv/bin/ruff check src tests
PYTHONPATH=src .venv/bin/python -m pytest -p no:warnings
./script/test_native.sh
```

## Documentación

- [Manual de instalación, uso y diagnóstico](docs/MANUAL_INSTALACION_CONFIGURACION.md)
- [Cambios de versión](CHANGELOG.md)
- [Pendientes y requisitos de salida de la 1.0](docs/V1_PENDIENTES.md)
- [Alcance oficial de la 1.0](packaging/JarvisV1Scope.json)
- [CLI de ingeniería](docs/ENGINEERING_CLI.md)
- [Guía de interfaz](docs/JARVIS_INTERFACE_STYLE.md)
- [Referencia técnica conservada](docs/REFERENCIA_TECNICA.md)
- [CI y falta de capacidad de runners](docs/quality/CI_CAPACITY.md)
