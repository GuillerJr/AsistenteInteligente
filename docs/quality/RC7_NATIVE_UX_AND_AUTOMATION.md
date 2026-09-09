# RC-7 — UX nativa y automatización silenciosa

RC-7 usa dos superficies reales sin requerir voz. Primero abre una aplicación AppKit efímera,
firmada ad hoc, y valida captura autorizada, lectura del árbol de accesibilidad, `AXPress`, foco y
aislamiento del puntero físico. Luego inicia Google Chrome firmado con un perfil temporal y sin
salida a internet; la automatización usa CDP sobre `127.0.0.1:9222` y verifica el DOM en memoria.

Los artefactos temporales viven bajo `/private/tmp`, se destruyen al terminar y nunca se mezclan
con el perfil personal del navegador.

## Criterio de cierre

- La captura y el árbol AX describen la misma ventana autorizada.
- Una acción AX produce un cambio verificable de estado.
- El puntero físico del propietario no cambia.
- Chrome usa perfil efímero, proxy cerrado y CDP de loopback.
- Los fallos de permisos o de firma bloquean la prueba.

La orden canónica es `./script/rc3_rc8_gate.sh`.
