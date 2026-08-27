# ADR-0135: propietario ligado al modelo vocal exacto

- Estado: aceptado
- Fases: 2, 3 y 5

## Decisión

1. Jarvis calcula localmente una huella SHA-256 determinista de todos los archivos regulares del
   `JarvisSpeakerIdentity.mlmodelc` activo. La ruta relativa, longitud y contenido forman un
   manifiesto con versión; un directorio vacío, un enlace simbólico o un error de lectura falla
   cerrado.
2. Una selección explícita de propietario persiste la etiqueta junto con la huella del modelo que
   la originó. Si el modelo se reemplaza o reentrena, esa selección deja de conceder continuidad
   privada aunque el modelo nuevo reutilice la misma etiqueta.
3. La ventana de identidad conserva la etiqueta obsoleta para explicar el cambio y ofrece una
   reconfirmación visible. Confirmar la liga a la huella nueva y rota la conversación vocal vigente.
4. Cada conversación privada persiste su etiqueta y huella. Solo se reutiliza cuando ambas
   coinciden con el propietario verificado y el modelo activo; el estado histórico sin huella se
   descarta al migrar.
5. Antes de clasificar cada turno, el transcriptor vuelve a calcular la huella del activo y compara
   con la observada por la app. Una carrera de reemplazo desactiva la marca de propietario para ese
   turno.
6. La huella nunca sale del Mac, no se registra y no convierte la identidad vocal en autenticación,
   autorización o aprobación.

## Motivo

Una etiqueta como `guillermo` identifica una clase dentro de un modelo, no al modelo mismo. Confiar
solo en el texto permitiría que un activo reemplazado heredara selección e historial privados. La
huella usa CryptoKit ya presente, no añade dependencias, red, polling ni otro modelo.

## Consecuencias

- Reentrenar o sustituir el clasificador exige una confirmación explícita cuando el propietario fue
  elegido manualmente.
- Un modelo nuevo con un único perfil puede resolver al propietario automáticamente, pero empieza
  una conversación privada nueva porque la huella anterior no coincide.
- Los modelos intactos no cambian el flujo ni añaden latencia apreciable.
