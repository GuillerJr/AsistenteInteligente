# ADR-0160: escritura visual ligada al foco

- Estado: aceptado
- Fases: 1, 3 y 4

## Evidencia

El helper validaba la aplicación y el campo al comenzar `type`, pero una frase de hasta 500
caracteres se emitía en varios eventos. Si el usuario o la aplicación movía el foco durante ese
intervalo, los fragmentos restantes podían llegar a otro campo o aplicación. La división fija de
UTF-16 también podía separar un par sustituto en el borde de 20 unidades.

## Decisión

1. El helper obtiene un único elemento Accessibility inicial y exige un rol de texto permitido, no
   seguro y no sensible.
2. Antes de cada fragmento vuelve a comprobar la app frontal, propietario, rol, subrol, sensibilidad
   e igualdad `CFEqual` con el elemento inicial.
3. Cualquier cambio termina como objetivo inseguro antes de publicar el fragmento restante. La
   comprobación exterior posterior a la acción se conserva.
4. `ComputerTextInputPlan` divide por `Character`, no por posiciones UTF-16. Mantiene grafemas
   completos y reconstrucción exacta; un grafema individual puede superar el tamaño objetivo.
5. El tamaño objetivo continúa en 20 unidades UTF-16 para conservar ciclos cortos de revalidación.
6. No usa portapapeles, `AXValue`, shell, almacenamiento, modelo, permiso ni dependencia adicional.

## Consecuencia

La escritura libre sigue ligada al texto literal del objetivo y ahora también al destino exacto
durante toda la emisión. La comprobación por fragmento reduce la ventana de cambio de foco sin
corromper Unicode ni aumentar llamadas NVIDIA; una carrera dentro de un único evento del sistema no
puede eliminarse, por lo que el flujo continúa fallando cerrado antes y después de cada acción.
