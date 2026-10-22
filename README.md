# Jarvis

Asistente personal para **macOS Apple Silicon**. Conversa por texto o voz, organiza
proyectos y tareas, y trabaja con tus apps mediante acciones que tú apruebas.
La interfaz está en español. La inferencia usa **NVIDIA exclusivamente** y requiere
internet; la ejecución de herramientas, los permisos y la memoria cifrada viven en el Mac.

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

Abre Jarvis, sigue la bienvenida e introduce tú mismo la clave NVIDIA en su campo
seguro. Puedes saltar los permisos opcionales y escribir tu primera pregunta.
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
