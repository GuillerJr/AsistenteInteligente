# ADR-0083: Adaptación privada del propietario y turnos por activación

- Estado: aceptado
- Fecha: 2026-08-26
- Reemplaza parcialmente: ADR-0082

## Filtro de ingeniería

1. **Cuestionar:** aprender no implica resumir cada conversación con otro LLM ni transcribir siempre.
2. **Eliminar:** no hay modelo de extracción, nube de perfiles, escucha de seguimiento ni nueva base.
3. **Simplificar:** patrones explícitos escriben ranuras deduplicadas en SQLite y seis hechos como
   máximo personalizan el prompt existente.
4. **Acelerar:** la extracción y recuperación son locales; el turno común conserva una inferencia.
5. **Automatizar:** el perfil se actualiza al completar el job y preferencias contradictorias
   reemplazan su ranura anterior.

## Decisión

Jarvis aprende solo afirmaciones explícitas del propietario sobre identidad, comunicación, gustos,
intereses y trabajo. Cada hecho incluye fuente estable y las etiquetas `owner-profile` más su
categoría. No se guardan secretos. Una afirmación de voz requiere que el modelo tenga un único perfil
y que este sea reconocido con confianza mínima de 0,78; con múltiples perfiles el aprendizaje por
voz falla cerrado. Esto reduce contaminación, pero no convierte la biometría en autenticación ni
autoridad. Órdenes explícitas permiten olvidar una ranura o todo el perfil.

El clasificador Core ML de «Jarvis» es la única ruta de audio anterior a la activación. Sus buffers
son efímeros y no generan texto. Apple Speech se abre solo tras detectar el nombre y se cierra al
terminar el turno; la siguiente intervención requiere repetir «Jarvis». La conversación conserva su
UUID, no una captura abierta. El clasificador continúa disponible en modo de bajo consumo para no
romper la invocación por nombre; reposo y presión térmica seria sí lo detienen.

NVIDIA Magpie mantiene la voz principal. Su presupuesto inicial es 1,8 segundos; después se usa una
voz española estándar de Apple. El fallback evita voces de personaje, reproduce a tono casi natural
y no ralentiza el resultado esperando indefinidamente al endpoint preview.

## Consecuencias

La personalización persiste entre reinicios sin añadir llamadas NVIDIA. Los hechos recuperados son
datos no confiables, nunca instrucciones. El usuario conserva borrado explícito y la ausencia de una
identidad reconocida impide aprendizaje hablado. La conversación requiere una activación por turno,
como frontera de privacidad deliberada.
