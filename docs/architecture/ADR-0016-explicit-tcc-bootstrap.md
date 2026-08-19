# ADR-0016: Solicitud TCC explícita de la app instalada

- Estado: aceptado
- Fecha: 2026-08-19

## Filtro de ingeniería

1. **Cuestionar:** editar la base TCC, automatizar Ajustes o añadir un instalador son enfoques
   frágiles y exceden el permiso del usuario.
2. **Eliminar:** no se añaden dependencias, entitlements ficticios, helper privilegiado ni captura
   automática.
3. **Simplificar:** un argumento de arranque solicita solo Micrófono y Speech aún indeterminados.
4. **Acelerar:** el script existente relanza el bundle ya instalado con esa opción.
5. **Automatizar:** una orden alcanza los diálogos nativos; aceptar o denegar siempre queda en manos
   del usuario.

## Decisión

`menu_bar_service.sh permissions` valida la firma, detiene la instancia activa y abre exactamente el
bundle de `~/Applications` con `--request-permissions`. La app solicita en serie únicamente estados
TCC `notDetermined` y luego continúa como menu bar app normal. Ninguna orden llama a `tccutil`, toca
la base de privacidad o registra audio antes de la autorización.

El bundle local conserva firma ad hoc. Su requisito designado depende del CDHash, por lo que una
recompilación puede requerir una nueva decisión TCC. Una identidad Developer ID estable solo será
necesaria al preparar distribución y notarización.

## Encaje en el roadmap

- **Fase 3:** desbloquea de forma auditable la captura y transcripción local opt-in.
- **Fase 5:** mantiene el arranque voice-first silencioso; la solicitud ocurre solo por orden
  explícita.
