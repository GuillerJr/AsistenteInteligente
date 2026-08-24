# ADR-0068: Panel táctico de Menu Bar

- Estado: aceptado
- Fecha: 2026-08-24

## Filtro de ingeniería

1. **Cuestionar:** una lista nativa larga ya no expresaba la jerarquía entre voz, sensores y HUD.
2. **Eliminar:** no se añade una ventana principal, navegación, preferencias ni dependencia visual.
3. **Simplificar:** el mismo `MenuBarExtra` cambia de `.menu` a `.window` y reutiliza el modelo actual.
4. **Acelerar:** una sola superficie compacta expone las acciones existentes sin flujo adicional.
5. **Automatizar:** el panel refleja el estado observable y el monitor actual sin sondeo propio.

## Decisión

Menu Bar presenta un panel SwiftUI de 348 pt con cabecera de estado, acción primaria contextual,
sensores, comandos multimodales y activación «Jarvis». Los permisos siguen siendo explícitos: solo
un botón de sensor o configuración puede solicitar TCC. El tema oscuro está aislado al panel para
mantener contraste en ambos modos de apariencia de macOS.

AppKit permanece limitado al panel físico del notch. Menu Bar utiliza exclusivamente la escena
SwiftUI y conserva `LSUIElement=true`, por lo que no aparece en el Dock ni abre UI al iniciar.

## Encaje en el roadmap

- **Fase 3:** hace legible el estado y la configuración de sensores.
- **Fase 5:** consolida la UX voice-first y mantiene HUD/notch como superficies bajo demanda.

## Consecuencias

Las acciones dejan de ser una lista lineal y se reconocen por función y estado. El panel tiene un
tamaño deliberadamente fijo; flujos largos continúan en las ventanas auxiliares existentes.

