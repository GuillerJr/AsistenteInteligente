# ADR-0124: Skills declarativas locales y sin autoridad

Fecha: 2026-08-26

## Decisión

1. Jarvis selecciona Skills con coincidencia determinista de frases y términos; no consume una
   inferencia adicional para enrutar.
2. Una Skill declara rol, disparadores, instrucciones, allowlist de herramientas y hasta tres
   herramientas iniciales. No contiene Python, shell, plugins ni ejecutores.
3. La allowlist debe ser subconjunto de las herramientas que el broker ya concede al rol. La Skill
   puede reducir el alcance de una solicitud, pero nunca crear una capacidad o permiso.
4. Las Skills aprendidas se validan con un contrato estricto, se guardan como JSON `0600` dentro de
   `~/Library/Application Support/Aegis/skills` y se detectan en la siguiente solicitud sin reiniciar
   el daemon.
5. Instrucciones con credenciales probables o lenguaje explícito para omitir política y
   confirmaciones se rechazan. Archivos dañados, demasiado grandes, enlazados o fuera del contrato
   se ignoran.
6. Las cinco Skills integradas pueden acompañar solicitudes remotas porque forman parte del código
   revisado. Las Skills aprendidas, sus instrucciones y su origen nunca se envían a NVIDIA.
7. El modelo continúa limitado a una propuesta de herramienta por turno. El broker autoriza y el
   usuario confirma las mutaciones exactamente igual que antes de activar Skills.

## Skills integradas

- `mac-control-expert`: control nativo y visual acotado de aplicaciones.
- `browser-navigation-expert`: investigación, lectura, apertura e interacción web.
- `security-audit-expert`: auditoría defensiva de código, red y postura de macOS.
- `code-review-expert`: revisión táctica basada en evidencia.
- `personal-productivity-expert`: correo, agenda, contactos y recordatorios.

## Consecuencias

- Jarvis gana especialización y vocabulario operativo sin un framework de plugins ni una llamada de
  routing adicional.
- El dueño puede enseñar procedimientos declarativos con un archivo corto y reversible.
- Una Skill aprendida con herramientas puede orientar localmente la selección y el alcance, pero su
  texto privado no cruza la frontera NVIDIA; el proveedor solo recibe la solicitud actual y los
  esquemas ya permitidos.
- Los flujos multiacción siguen fuera de este MVP para preservar confirmación exacta, auditoría y
  baja latencia.
