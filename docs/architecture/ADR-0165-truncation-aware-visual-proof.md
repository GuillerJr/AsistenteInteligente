# ADR-0165: prueba visual consciente de truncamiento

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

El presupuesto Accessibility puede terminar dos recorridos en posiciones distintas. Aunque la
interfaz no cambie, cada subconjunto AX produce otro digest semántico y una etiqueta omitida en la
captura anterior puede parecer evidencia nueva después de actuar. También deja de ser demostrable
que un control observado sea la única coincidencia local.

## Decisión

1. El estado perceptual conserva digest AX, firma visual y bandera `truncated`.
2. Un cambio semántico cuenta como progreso únicamente cuando ambos árboles AX están completos.
3. Si cualquiera está truncado, solo un cambio mínimo de 8 bits en la firma visual demuestra
   progreso; variaciones del subconjunto Accessibility se ignoran.
4. Después de una acción, la lista de evidencia nueva queda vacía si el estado anterior o actual está
   truncado. Una respuesta `done` se rechaza aunque cite un texto presente en el subconjunto actual.
5. El fast-path de clic por coincidencia única se desactiva con árbol parcial. Scroll y navegación
   exacta permanecen locales porque no dependen de unicidad perceptual.
6. La primera observación todavía puede citar evidencia AX exacta presente: el truncamiento impide
   demostrar novedad o ausencia, no la existencia del elemento ya validado.

## Consecuencia

La latencia acotada de Accessibility no introduce falsos progresos ni finalizaciones. Jarvis conserva
el camino visual cuando los píxeles prueban cambio suficiente y falla cerrado cuando la conclusión
depende de elementos que el recorrido parcial pudo omitir.
