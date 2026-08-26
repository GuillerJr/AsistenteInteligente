# ADR-0099: conciencia energética local bajo demanda

- Estado: aceptado
- Fases: 1, 3 y 5

## Decisión

1. Frases exactas y acotadas sobre batería crean una llamada local `system_power_status`, sin
   recuperar memoria ni invocar un modelo.
2. El ejecutor usa exclusivamente `/usr/bin/pmset -g batt`, sin shell, argumentos del usuario,
   entrada estándar ni entorno heredado. La consulta vence al segundo.
3. La salida se limita a 4 KiB, rechaza controles inesperados y se reduce a presencia, porcentaje,
   estado, fuente y minutos estimados. El identificador interno y el texto original se descartan.
4. El grafo valida ese contrato y publica una frase española como primer fragmento y resultado final
   bajo `local/deterministic-power`. Los fallos conocidos también terminan localmente.
5. No se añade monitor, temporizador, caché ni *polling*: `pmset` solo se ejecuta ante una petición
   explícita.
6. La autoevaluación clasifica todo resultado interno `local/deterministic-*` como determinista, no
   como NVIDIA.

## Motivo

El estado energético es local, volátil y autoritativo en macOS. Un LLM no puede conocerlo sin una
lectura y añadirlo después solo aumenta latencia y exposición. La consulta puntual conserva el
presupuesto térmico del MacBook Air y evita trabajo continuo del daemon. En 100 recorridos completos
del grafo sobre este Mac, incluyendo la invocación real a `pmset`, se observaron cero rondas de
modelo y 21,25 ms de latencia promedio.

## Límites

- La autonomía es una estimación de macOS y puede cambiar con la carga de trabajo.
- Jarvis informa estados contradictorios tal como se observan; no inventa una causa ni modifica la
  administración de energía.
- No consulta salud, ciclos, temperatura, número de serie ni perfiles privados de la batería.
- Preguntas abiertas sobre degradación, diagnóstico o recomendaciones conservan el cerebro híbrido.
