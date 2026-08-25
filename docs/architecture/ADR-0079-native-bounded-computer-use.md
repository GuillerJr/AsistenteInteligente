# ADR-0079: Control visual nativo, acotado y confirmado

- Estado: aceptado
- Fases: 1, 3, 4 y 5

## Decisión

Jarvis incorpora `computer_use` como una sola herramienta crítica del planner. Sus argumentos son
un objetivo explícito, un bundle ID exacto y un límite de uno a doce pasos. Cada llamada exige una
confirmación de un solo uso ligada al digest exacto; el ciclo completo cuenta como una llamada de
herramienta y termina en un máximo de 90 segundos.

La activación inicial espera hasta 15 segundos y reintenta de forma acotada mientras macOS termina
el arranque de la app. El cliente concede 20 segundos solo a esa orden; captura y acciones continúan
limitadas a ocho. Esta separación evita falsos fallos en arranques fríos sin relajar operaciones.

El daemon alterna una captura acotada con exactamente una decisión del rol NVIDIA de visión. Un
helper Swift ARM64 firmado usa ScreenCaptureKit y Accessibility para activar, observar y actuar solo
sobre la aplicación declarada. El helper vive como app anidada con identidad TCC estable, recibe JSON
estricto por stdin y no acepta shell, AppleScript, portapapeles ni ejecutables controlados por el
modelo.

Python y Swift bloquean de forma independiente Terminal, Finder, Mail, System Settings, Keychain,
Passwords y gestores de contraseñas. La frontera nativa verifica en cada acción la app al frente, el
propietario AX y el rol del campo. Campos seguros, etiquetas sensibles y atajos destructivos fallan
cerrados. Las capturas permanecen en memoria y se informa antes de aprobar que se enviarán a NVIDIA.

Screen Recording y Accessibility son opt-in desde la tarjeta `CONTROL`; el inicio normal nunca los
solicita. Mientras corre una sesión aprobada, la Menu Bar ofrece una cancelación inmediata ligada al
job activo.

## Aplicación del algoritmo de ingeniería

1. **Cuestionar:** controlar todo el equipo no es requisito; un objetivo y una app bastan.
2. **Eliminar:** no se incorporan Electron, Playwright, RPA, OCR local adicional ni un servicio
   residente nuevo.
3. **Simplificar:** captura, accesibilidad, firma y TCC usan APIs nativas de macOS.
4. **Acelerar:** observar, ejecutar una acción y verificar reutiliza el proveedor y el sistema de
   jobs existentes.
5. **Automatizar:** aprobada la sesión, el ciclo es autónomo hasta completar, bloquearse, cancelarse o
   alcanzar su límite.

## Consecuencias

- Jarvis puede navegar y operar interfaces visibles sin heredar cookies ni controlar Chrome por un
  canal privilegiado; interactúa con la sesión que el usuario ve.
- La calidad depende de la interpretación visual y de que la app exponga elementos Accessibility.
- Login, credenciales, compras, pagos, mensajes, envíos, descargas, permisos, seguridad y borrado
  permanecen fuera de alcance y requieren herramientas futuras con contratos específicos.
- Fase 1 aloja el ciclo y la política; Fase 3 aporta visión; Fase 4 impone las barreras; Fase 5 expone
  permisos, aprobación y parada de emergencia.
