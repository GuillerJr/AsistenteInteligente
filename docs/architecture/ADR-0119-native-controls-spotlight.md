# ADR-0119: controles nativos acotados y Spotlight local

## Estado

Aceptado.

## Decisión

1. Volumen y silencio se leen y modifican mediante CoreAudio. La herramienta acepta únicamente un
   porcentaje entre 0 y 100 y/o un booleano de silencio; toda modificación exige confirmación.
2. Multimedia admite solo `play_pause`, `next` y `previous`. Un script JXA fijo controla una única
   instancia activa compatible de Music o Spotify; cero o varias instancias fallan cerradas.
3. Spotlight usa `/usr/bin/mdfind -0 -onlyin HOME -interpret QUERY` sin shell. La consulta es un
   argumento, la salida está limitada a 128 KiB y el resultado a 20 entradas.
4. La lista excluye `Library`, `.Trash`, rutas ocultas, enlaces simbólicos, dispositivos y elementos
   fuera de la carpeta personal. Solo expone nombre, ruta y tipo; nunca contenido.
5. Abrir mediante Spotlight requiere confirmación y exactamente una coincidencia segura cuyo nombre
   o nombre sin extensión sea idéntico a la consulta.
6. Las órdenes inequívocas se parsean y responden localmente sin usar un modelo.

## Consecuencias

- Jarvis no mueve el puntero nativo ni genera teclas multimedia globales.
- Music y Spotify pueden solicitar Automatización en el primer control.
- Abrir por una consulta aproximada se rechaza deliberadamente; primero se busca y luego se formula
  una apertura exacta.
