# RC-8 — Soak final del candidato sin voz

RC-8 vuelve a medir el sistema después de las pruebas nativas para detectar fugas, bloqueos o
estado residual. Ejecuta 400 recorridos funcionales y 200 ciclos de IPC firmado contra el daemon
empaquetado. La prueba comprueba revisión, permisos, firma, latencia p95, crecimiento RSS y tareas
asíncronas pendientes.

## Criterio de cierre

- 20 ciclos completos de la matriz de 20 flujos: 400/400.
- 200 ciclos IPC sin corrupción, pérdida de secuencia o error HMAC.
- p95 IPC menor o igual a 250 ms.
- Crecimiento RSS y pico adicional menores o iguales a 8 MiB.
- Cero tareas asíncronas filtradas y cero llamadas de red.

El perfil biométrico del propietario sigue deliberadamente fuera de RC-8. P10 y P11 conservarán
esa certificación física para el final, tal como se acordó; esta exclusión aparece explícitamente
en el reporte privado `dist/Jarvis.rc3-rc8.json`.
