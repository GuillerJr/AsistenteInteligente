# ADR-0147: navegación visual segura sin inferencia

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Evidencia

El ciclo de `computer_use` ya resolvía localmente un clic accesible exacto, pero enviaba una captura
a NVIDIA para desplazamientos y teclas de navegación inequívocas. Además, una acción `wait` sumaba
su duración explícita y los 450 ms de estabilización destinados a acciones que sí cambian la UI.

## Decisión

1. Tras activar y observar la aplicación autorizada, órdenes exactas de desplazamiento y las teclas
   Escape, Tab, flechas, Inicio, Fin, Página arriba y Página abajo se ejecutan localmente.
2. El desplazamiento usa tres unidades por defecto o un entero explícito entre uno y ocho.
3. Enter, Espacio y todos los atajos modificados permanecen fuera de este fast-path porque pueden
   enviar formularios o activar acciones. El modelo conserva su contrato y la frontera nativa.
4. `secure_content` bloquea la sesión antes de cualquier fast-path. La aprobación de un solo uso,
   bundle ID, helper firmado, Accessibility y auditoría no cambian.
5. Una acción `wait` consume únicamente los milisegundos solicitados; no añade después la pausa fija
   de estabilización.
6. Después de cualquier fast-path ejecutado, una sola recaptura local debe diferir de la observación
   inicial. Un resultado idéntico termina como `blocked/uncertain_state` sin repetir la acción ni
   consultar al proveedor visual.

## Consecuencia

Las navegaciones simples evitan una inferencia visual y no envían el JPEG fuera del Mac. Añaden una
recaptura local para distinguir progreso de un no-op; no añaden tokens ni una segunda ejecución. Las
esperas remotas pierden 450 ms de cola artificial por paso. Las acciones ambiguas o con capacidad de
envío continúan pasando por NVIDIA y todas las barreras existentes.
