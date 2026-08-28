# ADR-0189: presentación contextual de aprobaciones

## Estado

Aceptada.

## Contexto

El backend detenía correctamente las acciones protegidas, pero el usuario debía abrir la Menu Bar y
después otra ventana para descubrir qué estaba esperando. En un asistente `voice-first`, esa fricción
parece una falta de respuesta y consume parte de los dos minutos de caducidad. La vista también usaba
una advertencia TCP genérica para varias herramientas que no operan sobre red.

## Decisión

1. Al pasar de ninguna aprobación a una `PendingApproval`, la escena nativa abre la ventana auxiliar
   singleton `approval`. La orden de voz que originó el job constituye la invocación explícita de
   esta superficie visual transitoria.
2. Cerrar la ventana no aprueba ni cancela. La Menu Bar mantiene una ruta para reabrirla y el job
   conserva su caducidad de dos minutos.
3. Solo los botones visibles `Denegar` y `Aprobar una vez` cambian estado. No se añade aprobación por
   voz, por notch, por temporizador ni por detección de identidad.
4. Título, icono y advertencia se derivan localmente del nombre de herramienta. Correo, calendario,
   recordatorios, contactos, audio, multimedia, Spotlight, navegador, aplicaciones, atajos, red,
   terminal y control visual explican su efecto real; una herramienta externa usa un aviso cerrado.
5. Resumen y advertencia admiten hasta cuatro líneas para que una URL o destino largo no quede
   oculto por truncamiento. El resumen sigue siendo seleccionable.
6. El modo DEBUG `--notch-preview approval` fabrica únicamente una estructura visual inerte para QA.
   No existe en Release y su identificador no corresponde a un job del daemon.

## Consecuencias

- El usuario revisa la acción inmediatamente sin convertir el notch en un panel de botones.
- La política, el digest, la confirmación de un solo uso y el ejecutor no cambian.
- Las advertencias dejan de atribuir conexiones TCP a operaciones de Mail, Calendar o aplicaciones.
- Una inspección visual del bundle verificó el ajuste completo de la URL y el estado naranja
  `ACCIÓN EN ESPERA` del notch.
