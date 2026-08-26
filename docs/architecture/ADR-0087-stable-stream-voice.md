# ADR-0087: timbre estable durante cada respuesta

- Estado: aceptado
- Fases: 3 y 5

## Decisión

1. NVIDIA Magpie continúa siendo la voz primaria y dispone de 1,8 segundos para iniciar cada
   segmento.
2. Si la credencial, el proveedor, el artefacto o la reproducción fallan, o se agota ese presupuesto,
   Jarvis conmuta inmediatamente a la mejor voz española local.
3. Después de conmutar, todos los segmentos restantes del mismo turno usan la voz local. El turno
   siguiente vuelve a intentar NVIDIA desde un estado limpio.

## Motivo

Reintentar el proveedor en cada frase introduce silencios repetidos y puede alternar timbres dentro
de una sola respuesta. Un fallback fijo por turno conserva continuidad y limita la latencia sin
añadir cachés, colas ni dependencias.

## Límites

- El audio remoto conserva su ciclo de vida efímero y se libera después de leerlo.
- La política no persiste texto, audio ni fallos entre turnos.
- Interrumpir la respuesta cancela ambas rutas y limpia el modo elegido.
