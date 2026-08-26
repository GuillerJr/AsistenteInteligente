# ADR-0089: intención explícita para el camino de herramientas

- Estado: aceptado
- Fases: 1, 4 y 5

## Decisión

1. El enrutador deja de inferir intención operativa por la presencia aislada de vocabulario como
   `hoy`, `app`, `clima`, `precio`, `correo` o `archivo`.
2. Una solicitud carga esquemas de herramientas cuando contiene un verbo operativo y un objeto de
   una capacidad admitida, una orden directa de investigación web o una consulta explícita de
   información pública actual.
3. La clasificación es local, determinista y basada en tokens completos. Incluye variantes comunes
   con y sin tilde producidas por dictado, sin incorporar un modelo, framework NLP o llamada extra.
4. La señal solo selecciona el proveedor y los esquemas. El broker, la política y la confirmación de
   un solo uso siguen siendo la frontera exclusiva de autorización y ejecución.

## Motivo

Las palabras sueltas producían falsos positivos: una frase como «¿cómo estás hoy?» omitía el cerebro
local y cargaba todos los esquemas del planner en NVIDIA. Exigir una intención compuesta reduce
latencia, tokens y red en el caso común sin conceder autoridad nueva al modelo.

## Límites

- El clasificador no interpreta semántica profunda ni resuelve referencias ambiguas.
- Un falso negativo responde sin herramientas y nunca ejecuta una acción; el usuario puede formular
  una orden explícita.
- Un falso positivo solo habilita esquemas: no evita validación, política, confirmación ni auditoría.
- Las capacidades nuevas deben añadir una combinación explícita y una prueba de regresión.
