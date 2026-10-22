# CI y capacidad de los runners

El proyecto requiere macOS arm64 para sus contratos Darwin y nativos. Los dos jobs
siguen usando `macos-26`; una falta de capacidad de GitHub no se convierte en una
prueba aprobada ni se oculta con `continue-on-error`.

Si un job no llega a ejecutar ningún paso y GitHub informa que no pudo asignarle
runner, registra el enlace al run como **infraestructura no disponible**. Reintenta
una vez cuando vuelva la capacidad, desde Actions › CI › Re-run failed jobs, o:

```bash
gh run rerun <run-id> --failed
```

El reintento conserva la revisión original. También está disponible Run workflow
(`workflow_dispatch`) para validar el `main` actual. Si el segundo intento tampoco
recibe runner, conserva el estado pendiente y la evidencia del pre-push local. No
crees commits vacíos ni un bucle de reintentos. Un fallo después de iniciar ruff,
pytest o Swift exige revisar el código y no se clasifica como falta de capacidad.

`actions/checkout@v5` utiliza Node 24 y necesita Actions Runner 2.327.1 o posterior;
los runners alojados lo administran por su cuenta. `astral-sh/setup-uv@v7` también
usa Node 24; la versión anterior emitía una advertencia de obsolescencia en el run real.

El job Swift selecciona explícitamente todas las pruebas del núcleo y construye
el auxiliar VAD real antes de ejecutarlas. El perfil predeterminado conserva también
las pruebas de interfaz, que se ejecutan localmente y en el pre-push. La extracción
de textos se activa con un directorio de salida explícito; las compilaciones nativas
normales consumen los recursos confirmados, sin intentar escribir en `/`.

Referencias: [checkout v5](https://github.com/actions/checkout/tree/v5),
[setup-uv v7](https://github.com/astral-sh/setup-uv/blob/v7/action.yml) y
[reintentar jobs](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs).
