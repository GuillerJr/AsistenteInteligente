# ADR-0042: Handoff explícito hacia la activación

- Estado: aceptado
- Fecha: 2026-08-21

## Filtro de ingeniería

1. **Cuestionar:** `dataset listo` no explica cómo producir e instalar el modelo que habilita la
   escucha real.
2. **Eliminar:** la aplicación no lanza Terminal, shell, compiladores ni procesos hijos.
3. **Simplificar:** se muestra el único comando atómico que ya existe.
4. **Acelerar:** un botón copia el texto exacto después del clic del usuario.
5. **Automatizar:** `activate_wake_word.sh` conserva validación, entrenamiento, empaquetado,
   instalación y comprobación del servicio.

## Decisión

Cuando `WakeWordEnrollmentProgress.isReady` es verdadero, la ventana de enrolamiento presenta
`./script/activate_wake_word.sh` como el siguiente paso. El texto es seleccionable y `Copiar comando`
lo escribe como texto plano en el portapapeles.

La condición depende del progreso validado, no del estado visual más reciente. Por ello, una muestra
adicional descartada no oculta el handoff si las 20+20 aceptadas continúan disponibles.

La app no intenta localizar el repositorio ni ejecutar el comando. Esto evita introducir rutas
absolutas, intérpretes, permisos de automatización o una segunda implementación del entrenador.

## Encaje en el roadmap

- **Fase 3 — Capacidades sensoriales:** conecta el dataset local con el detector entrenado.
- **Fase 5 — Voice-first:** hace visible el último paso previo a la escucha en segundo plano.

## Consecuencia

El usuario recibe una transición precisa y copiable sin ampliar la autoridad de la Menu Bar. El
entrenamiento continúa siendo local, visible en Terminal y explícitamente iniciado.
